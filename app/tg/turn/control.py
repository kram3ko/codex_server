"""Control-plane: cancel, auto-reset, emit_failure. State source — `turns` table."""

import contextlib

import structlog

from app.db.base import SessionLocal
from app.models import EventKind, TurnStatus
from app.services.codex import codex_remote
from app.services.codex.runner import quarantine_thread
from app.services.events.default import event_service
from app.services.sessions.store import ChatSession, cancel_session_turn
from app.services.turns.default import turn_service

log = structlog.get_logger(__name__)


async def cancel_turn(session: ChatSession) -> bool:
    """Stop button — interrupt codex active turn + cancel local task + finalize."""
    async with SessionLocal() as db:
        active = await turn_service.get_active_for_chat(db, session.db_chat_id)
    if active is not None and active.codex_turn_id is not None:
        with contextlib.suppress(Exception):
            await codex_remote.send_interrupt_turn_id(
                (active.sidecar or "admin") == "admin",
                active.codex_turn_id,
            )
    if not await cancel_session_turn(session):
        return False
    if active is not None:
        async with SessionLocal() as db:
            finalized = await turn_service.finalize_once(
                db,
                active.id,
                TurnStatus.CANCELLED,
                error_code="user_cancelled",
            )
            # Journal-event пишемо тільки якщо ми реально transition-нули turn.
            # Інакше runner вже finalize-нув і запис буде дублем.
            if finalized:
                await event_service.emit(
                    db,
                    EventKind.TURN_INTERRUPTED,
                    chat_id=session.db_chat_id,
                    user_id=session.db_user_id,
                )
            await db.commit()
    return True


async def auto_reset_thread(session: ChatSession) -> None:
    """Idle-timeout → quarantine current codex thread. Наступний turn відкриє свіжий."""
    async with SessionLocal() as db:
        active = await turn_service.get_active_for_chat(db, session.db_chat_id)
    broken_id = active.codex_thread_id if active else None
    await quarantine_thread(broken_id)
    async with SessionLocal() as db:
        await event_service.emit(
            db,
            EventKind.THREAD_RESET,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            payload={"reason": "auto_recovery_idle_timeout", "thread_id": broken_id},
        )
        await db.commit()
    log.warning("tg_thread_auto_reset", chat_id=session.db_chat_id, thread_id=broken_id)


async def emit_failure(
    session: ChatSession,
    *,
    code: str | None = None,
    detail: str | None = None,
    exc_type: str | None = None,
) -> None:
    payload = {k: v for k, v in (("code", code), ("detail", detail), ("exc_type", exc_type)) if v}
    async with SessionLocal() as db:
        await event_service.emit(
            db,
            EventKind.TURN_FAILED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            payload=payload or None,
        )
        await db.commit()
