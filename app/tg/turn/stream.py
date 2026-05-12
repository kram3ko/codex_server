"""Pipeline кодекс-event'ів для одного TG turn'у.

Споживає `session.client.run_turn`, маршалить події у progress / handlers.
Каузу idle-timeout / error / cancel розрулює caller (`runner._run_locked`),
а тут лише: idle watchdog, match-by-type, виклик outcomes.
"""

import structlog
from aiogram.types import Message

from app.config import settings
from app.services.bus.default import event_bus
from app.services.codex.collector import StreamCollector
from app.services.codex.events import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    iterate_with_idle_timeout,
)
from app.services.sessions.store import ChatSession
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn
from app.tg.progress import TurnProgressReporter
from app.tg.turn.control import emit_failure
from app.tg.turn.outcomes import handle_done, handle_dropped_stream

log = structlog.get_logger(__name__)


async def stream_turn(
    session: ChatSession,
    message: Message,
    prepared: PreparedTurn,
    progress: TurnProgressReporter,
) -> None:
    collector = StreamCollector()
    stream = session.client.run_turn(prepared.text, attachments=prepared.attachments)
    events_count = 0
    last_event_type = "none"

    async def _on_idle() -> None:
        log.error(
            "tg_codex_idle_timeout",
            chat_id=message.chat.id if message.chat else None,
            db_chat_id=session.db_chat_id,
            idle_timeout_s=settings.TG_TURN_TIMEOUT_SECONDS,
            events_count=events_count,
            last_event_type=last_event_type,
        )

    async for ev in iterate_with_idle_timeout(
        stream,
        settings.TG_TURN_TIMEOUT_SECONDS,
        on_idle=_on_idle,
    ):
        events_count += 1
        last_event_type = type(ev).__name__
        await event_bus.publish(session.db_chat_id, ev)
        collector.absorb(ev)
        match ev:
            case TokenEvent():
                if not prepared.had_voice_input:
                    await progress.note_partial(collector.buffer)
            case ToolCallEvent(name=name):
                await progress.note_tool(name)
            case ToolResultEvent(name=name, error=error):
                await progress.mark_tool_done(name, error=bool(error))
            case ErrorEvent(code=code, detail=detail):
                progress.mark_outcome("failed")
                await message.answer(
                    tg_markdown.escape(f"Codex error [{code}]: {detail or 'unknown error'}"),
                )
                await emit_failure(session, code=code, detail=detail)
                return
            case DoneEvent():
                await handle_done(
                    session,
                    message,
                    prepared,
                    collector.final_text or collector.buffer,
                    collector.attachments,
                    collector.tool_calls,
                    progress.committed_text,
                )
                return

    if not collector.done_seen:
        progress.mark_outcome("failed")
        await handle_dropped_stream(
            session,
            message,
            prepared,
            collector.buffer,
            collector.attachments,
            collector.tool_calls,
            progress.committed_text,
        )
