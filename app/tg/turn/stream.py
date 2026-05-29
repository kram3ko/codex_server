"""Pipeline кодекс-event'ів для одного TG turn'у.

Termin shape: stream_turn раз і завжди завершується `CodexTurnTerminal`
(COMPLETED/FAILED/CANCELLED) — runner мапить на `TurnStatus` без сліпого
COMPLETED після будь-якого return.
"""

import structlog
from aiogram.types import Message

from app.config import settings
from app.db.base import SessionLocal
from app.models import EventKind, TurnStatus
from app.services.bus.default import event_bus
from app.services.codex.client import CodexClient, StaleTurnStreamError
from app.services.codex.collector import StreamCollector
from app.services.codex.error_codes import CodexErrorCode
from app.services.codex.events import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.services.codex.runner import quarantine_thread
from app.services.codex.sidecar import SidecarName
from app.services.codex_usage import poller as usage_poller
from app.services.events.default import event_service
from app.services.mcp_authz import inject_authz
from app.services.sessions.store import ChatSession
from app.services.turns.default import turn_service
from app.services.turns.probe import (
    CodexTurnTerminal,
    interrupt_best_effort,
    probe_or_extend_idle,
)
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn
from app.tg.progress import TurnProgressReporter
from app.tg.turn.control import emit_failure
from app.tg.turn.outcomes import handle_done, handle_dropped_stream
from app.tg.turn.persistence import persist_assistant_turn

log = structlog.get_logger(__name__)


async def stream_turn(
    client: CodexClient,
    session: ChatSession,
    message: Message,
    prepared: PreparedTurn,
    progress: TurnProgressReporter,
    turn_id: int,
    sidecar: SidecarName,
) -> None:
    """Стрімить codex events. ЗАВЖДИ виходить через `CodexTurnTerminal`
    (success/failed/cancelled) — runner перетворює це на `TurnStatus`."""
    collector = StreamCollector()

    async def _on_started(codex_turn_id: str, thread_id: str) -> None:
        async with SessionLocal() as db:
            await turn_service.mark_running(db, turn_id, thread_id, codex_turn_id)
            await db.commit()

    events_count = 0
    last_event_type = "none"

    async def _on_idle() -> bool:
        if await probe_or_extend_idle(client, turn_id):
            return client.extend_idle_deadline()
        diagnostics = client.turn_diagnostics()
        log.error(
            "tg_codex_idle_timeout",
            chat_id=message.chat.id if message.chat else None,
            db_chat_id=session.db_chat_id,
            idle_timeout_s=settings.TG_TURN_TIMEOUT_SECONDS,
            events_count=events_count,
            last_event_type=last_event_type,
            **diagnostics,
        )
        await interrupt_best_effort(client, reason="tg_idle_timeout")
        return False

    async def _on_usage_signal() -> None:
        usage_poller.schedule_refresh(sidecar)

    authz_text = inject_authz(
        prepared.text,
        user_id=session.db_user_id,
        chat_id=session.db_chat_id,
        sidecar=sidecar,
    )
    stream = client.run_turn(
        authz_text,
        attachments=prepared.attachments,
        on_started=_on_started,
        idle_s=settings.TG_TURN_TIMEOUT_SECONDS,
        on_idle=_on_idle,
        on_usage_signal=_on_usage_signal,
    )

    try:
        async for ev in stream:
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
                    await message.answer(
                        tg_markdown.escape(f"Codex error [{code}]: {detail or 'unknown error'}"),
                    )
                    await emit_failure(session, code=code, detail=detail)
                    raise CodexTurnTerminal(
                        TurnStatus.FAILED,
                        error_code=code or CodexErrorCode.CODEX_ERROR,
                        detail=detail,
                    )
                case DoneEvent():
                    await handle_done(
                        session,
                        message,
                        prepared,
                        collector.final_text or collector.buffer,
                        collector.attachments,
                        collector.tool_calls,
                        progress.committed_text,
                        turn_id=turn_id,
                    )
                    raise CodexTurnTerminal(TurnStatus.COMPLETED)
    except CodexTurnTerminal:
        raise
    except StaleTurnStreamError as exc:
        await interrupt_best_effort(client, reason="tg_stale_turn_stream")
        await quarantine_thread(client.current_thread_id)
        if collector.buffer or collector.tool_calls or collector.attachments:
            await persist_assistant_turn(
                session,
                collector.buffer,
                collector.attachments,
                collector.tool_calls,
                partial=True,
            )
        async with SessionLocal() as db:
            await event_service.emit(
                db,
                EventKind.THREAD_RESET,
                chat_id=session.db_chat_id,
                user_id=session.db_user_id,
                payload={"reason": "stale_turn_stream", "diagnostics": exc.diagnostics},
            )
            await db.commit()
        await message.answer(
            tg_markdown.escape(
                "Codex завис — thread скинуто, історію (20 останніх "
                "повідомлень) буде відновлено на наступному turn'і. "
                "Повтори запит."
            )
        )
        raise CodexTurnTerminal(
            TurnStatus.FAILED,
            error_code=CodexErrorCode.TURN_TIMEOUT,
            detail="stale-turn-notifications",
        ) from exc

    # Stream завершився без done/error → STREAM_DROPPED.
    if not collector.done_seen:
        await handle_dropped_stream(
            session,
            message,
            prepared,
            collector.buffer,
            collector.attachments,
            collector.tool_calls,
            progress.committed_text,
        )
    raise CodexTurnTerminal(
        TurnStatus.FAILED,
        error_code=CodexErrorCode.STREAM_DROPPED,
        detail="codex stream ended without done/error",
    )
