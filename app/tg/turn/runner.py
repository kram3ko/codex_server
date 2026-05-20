"""TurnRunner — orchestration для TG-handler-а. `turns` table — source of truth.

Auto-steer: якщо `turn_service.get_active_for_chat` повертає row з `codex_turn_id`,
шлемо `send_steer_by_ids` у його sidecar; на reject — fall through до нового turn.
"""

import asyncio

import structlog
import websockets
from aiogram.types import Message

from app.config import settings
from app.db.base import SessionLocal
from app.models import TurnStatus
from app.services import rate_limit
from app.services.chats.default import chat_service
from app.services.codex import codex_remote
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.runner import open_codex_turn
from app.services.codex.sidecar import SidecarName
from app.services.codex.transport import AppServerError
from app.services.codex_usage import poller as usage_poller
from app.services.sessions.store import ChatSession
from app.services.stt.base import STTBackend
from app.services.turns import locks as turn_locks
from app.services.turns.default import turn_service
from app.services.turns.locks import LockAcquireOutcome
from app.services.turns.probe import CodexTurnTerminal
from app.services.turns.recovery import reconcile_if_stale
from app.services.turns.runner import heartbeat_loop
from app.services.turns.schemas import TurnCreate, TurnRow
from app.services.users.default import user_service
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn, prepare_turn
from app.tg.progress import TurnOutcome, TurnProgressReporter
from app.tg.sessions import ChatSessionStore
from app.tg.turn.control import auto_reset_thread, emit_failure
from app.tg.turn.persistence import persist_user_turn
from app.tg.turn.stream import stream_turn

log = structlog.get_logger(__name__)

_STEER_RPC_ERRORS = (
    AppServerError,
    websockets.WebSocketException,
    OSError,
    TimeoutError,
)


class TurnRunner:
    def __init__(self, sessions: ChatSessionStore, transcriber: STTBackend) -> None:
        self._sessions = sessions
        self._transcriber = transcriber

    async def handle(self, message: Message) -> None:
        if message.chat is None or message.from_user is None:
            return

        display_name = message.from_user.full_name or message.from_user.username
        async with SessionLocal() as db:
            user = await user_service.get_or_create_by_tg(db, message.from_user.id, display_name)
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

        try:
            await rate_limit.reserve_turn(user)
        except rate_limit.RateLimited as exc:
            await message.answer(
                f"⏳ Ліміт перевищено ({exc.scope}={exc.limit}). Спробуй трохи пізніше."
            )
            return

        try:
            if await _handle_active_tg_turn(session, prepared, message):
                return

            user_msg_id = await persist_user_turn(session, prepared)
            sidecar = SidecarName.ADMIN if session.is_admin else SidecarName.GUEST
            async with SessionLocal() as db:
                turn = await turn_service.try_create_starting(
                    db,
                    TurnCreate(
                        chat_id=session.db_chat_id,
                        user_id=session.db_user_id,
                        user_message_id=user_msg_id,
                        sidecar=sidecar,
                    ),
                )
                if turn is not None:
                    await db.commit()
            if turn is None:
                # Active turn у цьому chat ще не finalize-нувся (steer тільки
                # що пройшов і відпустить slot за мить, або turn у STARTING
                # без codex_turn_id ще). Просимо retry.
                log.warning("tg_turn_create_race_lost", chat_id=session.db_chat_id)
                await message.answer(
                    tg_markdown.escape("⏳ У цьому chat вже виконується turn — спробуй за мить.")
                )
                return

            progress = TurnProgressReporter(message)
            await progress.start()
            try:
                await self._run_locked(session, message, prepared, progress, turn)
            finally:
                await progress.stop()
        finally:
            await rate_limit.release_turn(user)

    async def _run_locked(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
        progress: TurnProgressReporter,
        turn: TurnRow,
    ) -> None:
        sidecar = SidecarName.normalize(
            turn.sidecar or (SidecarName.ADMIN if session.is_admin else SidecarName.GUEST),
        )
        async with session.turn_lock:
            current = asyncio.current_task()
            session.current_turn_task = current
            terminal: TurnStatus | None = None
            terminal_error: tuple[str | None, str | None] = (None, None)
            try:
                # Per-chat active lock — фізична гарантія "1 active turn per chat".
                # Різні chat-и працюють паралельно (codex-cli тримає окремий thread/WS).
                async with turn_locks.hold_turn_locks(session.db_chat_id, turn.id) as outcome:
                    if outcome != LockAcquireOutcome.ACQUIRED:
                        terminal = TurnStatus.FAILED
                        terminal_error = (
                            CodexErrorCode.TURN_BUSY,
                            f"lock unavailable: {outcome.value}",
                        )
                        progress.mark_outcome(TurnOutcome.FAILED)
                        await message.answer(
                            tg_markdown.escape(
                                "⏳ У цьому chat вже виконується turn — спробуй за мить."
                            )
                        )
                        return
                    heartbeat_task = asyncio.create_task(
                        heartbeat_loop(turn.id, session.db_chat_id),
                        name=f"tg-turn-heartbeat:{turn.id}",
                    )
                    try:
                        async with open_codex_turn(
                            session.db_chat_id, is_admin=session.is_admin
                        ) as client:
                            await stream_turn(
                                client, session, message, prepared, progress, turn.id, sidecar
                            )
                            # `stream_turn` ЗАВЖДИ raise-ить `CodexTurnTerminal`.
                            # Цей рядок не повинен бути reachable; якщо ми тут —
                            # це bug у stream_turn (відсутній terminal-raise).
                            log.error(
                                "tg_stream_turn_no_terminal",
                                turn_id=turn.id,
                            )
                            terminal = TurnStatus.FAILED
                            terminal_error = (
                                CodexErrorCode.STREAM_DROPPED,
                                "stream_turn returned without raising terminal",
                            )
                    finally:
                        heartbeat_task.cancel()
                        try:
                            await heartbeat_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            log.exception("tg_heartbeat_task_failed", turn_id=turn.id)
            except CodexTurnTerminal as exc:
                terminal = exc.status
                if exc.status != TurnStatus.COMPLETED:
                    terminal_error = (exc.error_code, exc.detail)
            except TimeoutError:
                terminal = TurnStatus.FAILED
                terminal_error = (
                    CodexErrorCode.TURN_TIMEOUT,
                    f"idle>{settings.TG_TURN_TIMEOUT_SECONDS}s",
                )
                await self._on_timeout(session, message, progress)
            except asyncio.CancelledError:
                terminal = TurnStatus.CANCELLED
                progress.mark_outcome(TurnOutcome.INTERRUPTED)
                raise
            except Exception as exc:
                terminal = TurnStatus.FAILED
                terminal_error = (CodexErrorCode.CODEX_ERROR, str(exc))
                await self._on_unexpected(session, message, progress, exc)
            finally:
                if session.current_turn_task is current:
                    session.current_turn_task = None
                if terminal is not None:
                    async with SessionLocal() as db:
                        await turn_service.finalize_once(
                            db,
                            turn.id,
                            terminal,
                            error_code=terminal_error[0],
                            error_detail=terminal_error[1],
                        )
                        await db.commit()
                    # Event-driven usage refresh — codex списав tokens на
                    # finalize. Симетрично до `execute_turn_inner` (web path).
                    usage_poller.schedule_refresh(sidecar)

    @staticmethod
    async def _on_timeout(
        session: ChatSession,
        message: Message,
        progress: TurnProgressReporter,
    ) -> None:
        progress.mark_outcome(TurnOutcome.FAILED)
        log.error(
            "tg_codex_timeout",
            chat_id=message.chat.id if message.chat else None,
            idle_timeout_s=settings.TG_TURN_TIMEOUT_SECONDS,
        )
        await emit_failure(
            session,
            code=CodexErrorCode.TURN_TIMEOUT,
            detail=f"idle>{settings.TG_TURN_TIMEOUT_SECONDS}s",
        )
        await auto_reset_thread(session)
        await message.answer(
            tg_markdown.escape(
                "Codex завис — thread скинуто, історію (20 останніх "
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
        progress.mark_outcome(TurnOutcome.FAILED)
        log.error(
            "tg_codex_failed",
            exc_type=type(exc).__name__,
            error=str(exc),
            chat_id=message.chat.id if message.chat else None,
        )
        await message.answer(tg_markdown.escape(f"Помилка: {exc}"))
        await emit_failure(session, exc_type=type(exc).__name__, detail=str(exc))


async def _handle_active_tg_turn(
    session: ChatSession,
    prepared: PreparedTurn,
    message: Message,
) -> bool:
    """Симетрія до web `_handle_active_turn`: steer / interrupt / BUSY.
    Returns True якщо handled (steer success або BUSY-message);
    False = немає active turn-а, caller створює новий."""
    async with SessionLocal() as db:
        active = await turn_service.get_active_for_chat(db, session.db_chat_id)
    if active is not None and await reconcile_if_stale(active):
        active = None
    if active is None:
        return False

    if active.codex_turn_id is None:
        log.warning("tg_active_pending_conflict", chat_id=session.db_chat_id)
        await message.answer(tg_markdown.escape("⏳ Turn ще запускається — спробуй за мить."))
        return True

    if prepared.attachments:
        log.warning("tg_active_with_uploads", chat_id=session.db_chat_id)
        await message.answer(
            tg_markdown.escape("⏳ Попередній turn ще завершується — спробуй за мить.")
        )
        return True

    is_admin_sidecar = SidecarName.normalize(active.sidecar) is SidecarName.ADMIN

    if prepared.text:
        try:
            accepted = await codex_remote.send_steer_by_ids(
                chat_id=session.db_chat_id,
                is_admin=is_admin_sidecar,
                thread_id=active.codex_thread_id,
                codex_turn_id=active.codex_turn_id,
                text=prepared.text,
            )
        except _STEER_RPC_ERRORS as exc:
            log.warning("tg_inline_steer_rpc_failed", error=str(exc))
            accepted = False
        if accepted:
            await persist_user_turn(session, prepared)
            # Bump AFTER persist — stream-loop persist_segment гарантовано
            # побачить counter тільки коли USER row уже у БД.
            await codex_remote.bump_steer_count(active.id)
            return True

    try:
        interrupted = await codex_remote.send_interrupt_turn_id(
            is_admin_sidecar,
            active.codex_turn_id,
        )
    except _STEER_RPC_ERRORS as exc:
        log.warning("tg_inline_interrupt_rpc_failed", error=str(exc))
        interrupted = False

    if not interrupted:
        await message.answer(
            tg_markdown.escape("⏳ Попередній turn ще завершується — спробуй за мить.")
        )
        return True
    return False
