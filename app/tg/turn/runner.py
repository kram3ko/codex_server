"""TurnRunner — тонка orchestration-точка для TG-handler'а.

Відповідає тільки за: user/chat bootstrap, prepare_turn, lock'инг + lifecycle
прогресу, виклик stream_turn. Streaming, persistence, outcome — у сусідніх
модулях.
"""

import asyncio
import contextlib

import structlog
from aiogram.types import Message

from app.config import settings
from app.db.base import SessionLocal
from app.services.chats.default import chat_service
from app.services.sessions.store import ChatSession
from app.services.stt.base import STTBackend
from app.services.users.default import user_service
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn, prepare_turn
from app.tg.progress import TurnProgressReporter
from app.tg.sessions import ChatSessionStore
from app.tg.turn.control import auto_reset_thread, emit_failure, try_steer
from app.tg.turn.persistence import persist_user_turn
from app.tg.turn.stream import stream_turn

log = structlog.get_logger(__name__)


class TurnRunner:
    def __init__(self, sessions: ChatSessionStore, transcriber: STTBackend) -> None:
        self._sessions = sessions
        self._transcriber = transcriber

    async def handle(self, message: Message) -> None:
        if message.chat is None or message.from_user is None:
            return

        # Resolve user + chat FIRST — `prepare_turn` потребує обидва id для
        # scope'у uploads rows. Idempotent — session bootstrap re-uses їх.
        display_name = message.from_user.full_name or message.from_user.username
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(
                db, message.from_user.id, display_name
            )
            chat = await chat_service.get_or_create_for_tg(db, user.id, message.chat.id)
            db_user_id = user.id
            db_chat_id = chat.id
            await db.commit()

        prepared = await prepare_turn(
            message,
            self._transcriber,
            db_user_id=db_user_id,
            db_chat_id=db_chat_id,
        )
        log.info(
            "tg_prepared_turn",
            chat_id=message.chat.id,
            message_id=message.message_id,
            attachments=len(prepared.attachments),
            text_len=len(prepared.text),
        )
        if not prepared.text and not prepared.attachments:
            return

        session = await self._sessions.get_or_open(
            tg_user_id=message.from_user.id,
            tg_chat_id=message.chat.id,
            display_name=display_name,
        )

        if session.consume_steer():
            steered = await try_steer(session, message, prepared)
            if steered:
                return

        await persist_user_turn(session, prepared)

        progress = TurnProgressReporter(message)
        await progress.start()
        try:
            await self._run_locked(session, message, prepared, progress)
        finally:
            await progress.stop()

    async def _run_locked(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
        progress: TurnProgressReporter,
    ) -> None:
        async with session.turn_lock:
            await ChatSessionStore.seed_history_if_fresh_thread(session)
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                await stream_turn(session, message, prepared, progress)
            except TimeoutError:
                await self._on_timeout(session, message, progress)
            except asyncio.CancelledError:
                progress.mark_outcome("interrupted")
                raise
            except Exception as exc:  # noqa: BLE001 — backstop для TG turn, не валити поллер
                await self._on_unexpected(session, message, progress, exc)
            finally:
                if session.current_turn_task is current:
                    session.current_turn_task = None

    @staticmethod
    async def _on_timeout(
        session: ChatSession,
        message: Message,
        progress: TurnProgressReporter,
    ) -> None:
        progress.mark_outcome("failed")
        log.error(
            "tg_codex_timeout",
            chat_id=message.chat.id if message.chat else None,
            idle_timeout_s=settings.TG_TURN_TIMEOUT_SECONDS,
        )
        with contextlib.suppress(Exception):
            await session.client.interrupt()
        await emit_failure(
            session,
            code="turn_timeout",
            detail=f"idle>{settings.TG_TURN_TIMEOUT_SECONDS}s",
        )
        await auto_reset_thread(session)
        await message.answer(
            tg_markdown.escape(
                "Codex завис — thread скинуто, історію (10 останніх "
                "повідомлень) буде відновлено на наступному turn'і. "
                "Повтори запит.",
            )
        )

    @staticmethod
    async def _on_unexpected(
        session: ChatSession,
        message: Message,
        progress: TurnProgressReporter,
        exc: Exception,
    ) -> None:
        progress.mark_outcome("failed")
        log.error(
            "tg_codex_failed",
            exc_type=type(exc).__name__,
            error=str(exc),
            chat_id=message.chat.id if message.chat else None,
        )
        await message.answer(tg_markdown.escape(f"Помилка: {exc}"))
        await emit_failure(session, exc_type=type(exc).__name__, detail=str(exc))
