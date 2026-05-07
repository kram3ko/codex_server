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
from app.services.bus.default import event_bus
from app.services.codex.events import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex.history import messages_to_history_items
from app.services.events.default import event_service
from app.services.messages.default import message_service
from app.services.stt.base import STTBackend
from app.services.uploads.default import upload_service
from app.tg.formatting import tg_html
from app.tg.media import PreparedTurn, cleanup_attachments, prepare_turn
from app.tg.output import AudioChunk, PhotoChunk, parse_final_text, send_chunks
from app.tg.progress import TurnProgressReporter
from app.tg.sessions import ChatSession, ChatSessionStore

log = structlog.get_logger(__name__)

# 5 пар (USER+ASSISTANT) = 10 messages у seed-history.
_HISTORY_REPLAY_LIMIT = 10


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

        if session.consume_steer():
            steered = await self._try_steer(session, message, prepared)
            if steered:
                return

        await self._persist_user_turn(session, prepared)

        progress = TurnProgressReporter(message)
        await progress.start()
        try:
            await self._run_locked(session, message, prepared, progress)
        finally:
            await progress.stop()
            await cleanup_attachments(prepared.cleanup_paths)

    @staticmethod
    async def _seed_history_if_fresh_thread(session: ChatSession) -> None:
        """Якщо наступний run_turn відкриватиме новий thread — inject DB history."""
        if session.client.current_thread_id is not None:
            return
        async with SessionLocal() as db:
            recent = await message_service.list_recent(
                db, session.db_chat_id, limit=_HISTORY_REPLAY_LIMIT,
            )
        if recent and recent[-1].role is MessageRole.USER:
            recent = recent[:-1]
        items = messages_to_history_items(recent)
        if not items:
            return
        await session.client.ensure_thread()
        await session.client.inject_history(items)

    async def _try_steer(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
    ) -> bool:
        """Append text to running turn. Returns True on success — caller skips
        opening a new turn. False → fall through to a normal turn."""
        if session.current_turn_task is None or not prepared.text:
            return False
        ok = await session.client.steer(prepared.text)
        if not ok:
            await message.answer(tg_html("Не вдалось додати — turn уже завершився"))
            return False
        await self._persist_user_turn(session, prepared)
        return True

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
        progress: TurnProgressReporter,
    ) -> None:
        async with session.turn_lock:
            await self._seed_history_if_fresh_thread(session)
            current = asyncio.current_task()
            session.current_turn_task = current
            try:
                await self._stream_turn(session, message, prepared, progress)
            except asyncio.CancelledError:
                progress.mark_outcome("interrupted")
                raise
            except Exception as exc:  # noqa: BLE001
                progress.mark_outcome("failed")
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
        progress: TurnProgressReporter,
    ) -> None:
        buffer = ""
        tool_calls: list[dict] = []
        # Markdown-форматовані результати tool'ів (image_generation повертає
        # `![image](url)` тут) — використовуємо як fallback коли модель не
        # шле власний agentMessage з посиланням на згенеровану картинку.
        tool_outputs: list[str] = []
        done_seen = False

        async for ev in session.client.run_turn(prepared.text, attachments=prepared.attachments):
            await event_bus.publish(session.db_chat_id, ev)
            match ev:
                case TokenEvent(delta=delta):
                    buffer += delta
                    await progress.note_partial(buffer)
                case ToolCallEvent(name=name):
                    await progress.note_tool(name)
                case ToolResultEvent(name=name, result=result, error=error):
                    if result:
                        tool_outputs.append(result)
                    await progress.mark_tool_done(name, error=bool(error))
                case ErrorEvent(code=code, detail=detail):
                    progress.mark_outcome("failed")
                    await message.answer(
                        tg_html(f"Codex error [{code}]: {detail or 'unknown error'}"),
                    )
                    await self._emit_failure(session, code=code, detail=detail)
                    return
                case DoneEvent(final_text=final_text):
                    done_seen = True
                    composed = _compose_final_text(final_text, buffer, tool_outputs)
                    await self._handle_done(
                        session,
                        message,
                        prepared,
                        composed,
                        buffer,
                        tool_calls,
                        progress.committed_text,
                    )
                    return

        if not done_seen:
            progress.mark_outcome("failed")
            await self._handle_dropped_stream(
                session, message, buffer, tool_calls, progress.committed_text,
            )

    async def _handle_done(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
        final_text: str,
        buffer: str,
        tool_calls: list[dict],
        committed_prefix: str,
    ) -> None:
        if not final_text.strip():
            await self._handle_empty_response(session, message, prepared, buffer)
            return
        await self._send_response(message, final_text, committed_prefix)
        await self._persist_assistant_turn(session, final_text, tool_calls)

    async def _handle_empty_response(
        self,
        session: ChatSession,
        message: Message,
        prepared: PreparedTurn,
        buffer: str,
    ) -> None:
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

    async def _handle_dropped_stream(
        self,
        session: ChatSession,
        message: Message,
        buffer: str,
        tool_calls: list[dict],
        committed_prefix: str,
    ) -> None:
        log.warning(
            "tg_codex_stream_ended_without_done",
            chat_id=message.chat.id,
            buffer_len=len(buffer),
        )
        tail = buffer.strip()
        if not tail:
            await self._announce_empty_drop(session, message)
            return
        await self._send_response(message, tail, committed_prefix)
        await self._persist_partial_assistant_turn(session, tail, tool_calls)
        await message.answer(
            tg_html("⚠ Stream dropped before completion. Use /reset to reopen session."),
        )

    async def _announce_empty_drop(
        self,
        session: ChatSession,
        message: Message,
    ) -> None:
        await message.answer(
            tg_html("Codex stream dropped before any reply. Use /reset and try again."),
        )
        await self._emit_failure(session, code="stream_dropped", detail="no buffer")

    async def _send_response(
        self,
        message: Message,
        final_text: str,
        committed_prefix: str = "",
    ) -> None:
        remainder = _strip_committed_prefix(final_text, committed_prefix)
        if not remainder:
            return
        await self._dispatch_chunks(message, remainder)

    @staticmethod
    async def _dispatch_chunks(message: Message, text: str) -> None:
        chunks = parse_final_text(text)
        if not chunks:
            await message.answer(tg_html(text))
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
            upload_ids = await upload_service.persist_codex_outputs(
                db, session.db_chat_id, final_text,
            )
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
            upload_ids = await upload_service.persist_codex_outputs(
                db, session.db_chat_id, final_text,
            )
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


def _compose_final_text(final_text: str, buffer: str, tool_outputs: list[str]) -> str:
    base = final_text or buffer
    tool_text = "\n\n".join(t for t in tool_outputs if t.strip())
    if not base:
        return tool_text
    if not tool_text or _has_media(base) or not _has_media(tool_text):
        return base
    return f"{base.rstrip()}\n\n{tool_text}"


def _has_media(text: str) -> bool:
    return any(isinstance(chunk, (PhotoChunk, AudioChunk)) for chunk in parse_final_text(text))


def _strip_committed_prefix(text: str, committed: str) -> str:
    """Return only the portion of `text` that wasn't streamed yet as bubbles.

    `committed` is a strict prefix of the assistant text we already published
    via `progress.note_partial`. Sometimes the model rewrites earlier tokens
    (rare with Codex но trapляється) — у такому разі prefix не співпаде, і ми
    свідомо повертаємо повний `text`, нехай дублюється, ніж проковтнути final.
    """
    if not committed:
        return text
    if not text.startswith(committed):
        return text
    return text[len(committed):].lstrip()
