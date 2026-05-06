"""Run a single Codex turn for one Telegram message.

Persists user message before run, assistant message after, and emits
events (TURN_STARTED/COMPLETED/FAILED) to the journal. Storage is fire-and-
return — callers (handlers) are kept thin.
"""

import asyncio
import contextlib

import structlog
from aiogram.types import Message

from app.db.base import SessionLocal
from app.models import EventKind, MessageRole
from app.services.codex.events import DoneEvent, ErrorEvent, TokenEvent, ToolResultEvent
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.stt.base import STTBackend
from app.services.uploads.persist import persist_codex_outputs
from app.tg.formatting import tg_html
from app.tg.media import PreparedTurn, cleanup_attachments, prepare_turn
from app.tg.output import parse_final_text, send_chunks
from app.tg.progress import TurnProgressReporter
from app.tg.sessions import ChatSession, ChatSessionStore

log = structlog.get_logger(__name__)


class TurnRunner:
    def __init__(self, sessions: ChatSessionStore, transcriber: STTBackend) -> None:
        self._sessions = sessions
        self._transcriber = transcriber

    async def handle(self, message: Message) -> None:
        if message.chat is None or message.from_user is None:
            return

        prepared = await prepare_turn(message, self._transcriber)
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
            display_name=message.from_user.full_name or message.from_user.username,
        )
        await self._persist_user_turn(session, prepared)

        progress = TurnProgressReporter(message)
        await progress.start()
        try:
            await self._run_locked(session, message, prepared)
        finally:
            await progress.stop()
            await cleanup_attachments(prepared.cleanup_paths)

    async def _persist_user_turn(self, session: ChatSession, prepared: PreparedTurn) -> None:
        meta = {"attachments": list(prepared.attachments)} if prepared.attachments else None
        async with SessionLocal() as db:
            await message_service.append(
                db,
                session.db_chat_id,
                MessageRole.USER,
                prepared.text,
                meta=meta,
            )
            await event_service.emit(
                db,
                EventKind.TURN_STARTED,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
                payload={
                    "text_len": len(prepared.text),
                    "attachments": len(prepared.attachments),
                },
            )
            await db.commit()

    async def _run_locked(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
    ) -> None:
        async with session.turn_lock:
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                await self._stream_turn(session, message, prepared)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "tg_codex_failed",
                    exc_type=type(exc).__name__,
                    error=str(exc),
                    chat_id=message.chat.id,
                )
                await message.answer(tg_html(f"{type(exc).__name__}: {exc}"))
                await self._emit_failure(session, exc_type=type(exc).__name__, detail=str(exc))
            finally:
                if session.current_turn_task is current:
                    session.current_turn_task = None

    async def _stream_turn(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
    ) -> None:
        buffer = ""
        tool_calls: list[dict] = []
        # Markdown-форматовані результати tool'ів (image_generation повертає
        # `![image](url)` тут) — використовуємо як fallback коли модель не
        # шле власний agentMessage з посиланням на згенеровану картинку.
        tool_outputs: list[str] = []
        done_seen = False

        async for ev in session.client.run_turn(prepared.text, attachments=prepared.attachments):
            match ev:
                case TokenEvent(delta=delta):
                    buffer += delta
                case ToolResultEvent(result=result):
                    if result:
                        tool_outputs.append(result)
                case ErrorEvent(code=code, detail=detail):
                    await message.answer(
                        tg_html(f"Codex error [{code}]: {detail or 'unknown error'}"),
                    )
                    await self._emit_failure(session, code=code, detail=detail)
                    return
                case DoneEvent(final_text=final_text):
                    done_seen = True
                    fallback = final_text or buffer or "\n\n".join(tool_outputs)
                    await self._handle_done(session, message, prepared,
                                            fallback, buffer, tool_calls)
                    return

        if not done_seen:
            await self._handle_dropped_stream(session, message, buffer, tool_calls)

    async def _handle_done(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
        final_text: str,
        buffer: str,
        tool_calls: list[dict],
    ) -> None:
        if not final_text.strip():
            log.warning(
                "tg_empty_response",
                attachments=len(prepared.attachments),
                text_len=len(prepared.text),
                buffer_len=len(buffer),
            )
            await message.answer(
                tg_html(
                    "Codex returned empty response. "
                    "Try adding a caption or send the image again.",
                ),
            )
            await self._emit_failure(session, code="empty_response", detail="no final text")
            return
        await self._send_response(message, final_text)
        await self._persist_assistant_turn(session, final_text, tool_calls)

    async def _handle_dropped_stream(
        self,
        session: ChatSession,
        message: Message,
        buffer: str,
        tool_calls: list[dict],
    ) -> None:
        log.warning(
            "tg_codex_stream_ended_without_done",
            chat_id=message.chat.id,
            buffer_len=len(buffer),
        )
        tail = buffer.strip()
        if tail:
            await self._send_response(message, tail)
            await self._persist_partial_assistant_turn(session, tail, tool_calls)
            await message.answer(
                tg_html("⚠ Stream dropped before completion. Use /reset to reopen session."),
            )
            return
        await message.answer(
            tg_html("Codex stream dropped before any reply. Use /reset and try again."),
        )
        await self._emit_failure(session, code="stream_dropped", detail="no buffer")

    @staticmethod
    async def _send_response(message: Message, final_text: str) -> None:
        chunks = parse_final_text(final_text)
        if not chunks:
            await message.answer(tg_html(final_text))
            return
        bot = message.bot
        if bot is None or message.chat is None:
            return
        await send_chunks(bot, message.chat.id, chunks)

    @staticmethod
    async def _persist_assistant_turn(
        session: ChatSession,
        final_text: str,
        tool_calls: list[dict],
    ) -> None:
        async with SessionLocal() as db:
            upload_ids = await persist_codex_outputs(db, session.db_chat_id, final_text)
            meta: dict = {}
            if tool_calls:
                meta["calls"] = tool_calls
            if upload_ids:
                meta["upload_ids"] = upload_ids
            await message_service.append(
                db, session.db_chat_id, MessageRole.ASSISTANT, final_text, meta=meta or None,
            )
            await event_service.emit(
                db,
                EventKind.TURN_COMPLETED,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
                payload={
                    "final_text_len": len(final_text),
                    "tool_calls": len(tool_calls),
                    "uploads": len(upload_ids),
                },
            )
            await db.commit()

    @staticmethod
    async def _persist_partial_assistant_turn(
        session: ChatSession,
        final_text: str,
        tool_calls: list[dict],
    ) -> None:
        async with SessionLocal() as db:
            upload_ids = await persist_codex_outputs(db, session.db_chat_id, final_text)
            meta: dict = {"partial": True}
            if tool_calls:
                meta["calls"] = tool_calls
            if upload_ids:
                meta["upload_ids"] = upload_ids
            await message_service.append(
                db, session.db_chat_id, MessageRole.ASSISTANT, final_text, meta=meta,
            )
            await event_service.emit(
                db,
                EventKind.TURN_FAILED,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
                payload={
                    "reason": "stream_dropped",
                    "partial_text_len": len(final_text),
                    "tool_calls": len(tool_calls),
                },
            )
            await db.commit()

    @staticmethod
    async def _emit_failure(
        session: ChatSession,
        *,
        code: str | None = None,
        detail: str | None = None,
        exc_type: str | None = None,
    ) -> None:
        raw = {"code": code, "detail": detail, "exc_type": exc_type}
        payload = {k: v for k, v in raw.items() if v}
        async with SessionLocal() as db:
            await event_service.emit(
                db,
                EventKind.TURN_FAILED,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
                payload=payload or None,
            )
            await db.commit()


async def cancel_turn(session: ChatSession) -> bool:
    """Best-effort cancel current turn. Returns True if anything was cancelled."""
    task = session.current_turn_task
    if task is None:
        return False
    with contextlib.suppress(Exception):
        await session.client.interrupt()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    if session.current_turn_task is task:
        session.current_turn_task = None
    async with SessionLocal() as db:
        await event_service.emit(
            db,
            EventKind.TURN_INTERRUPTED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
        )
        await db.commit()
    return True
