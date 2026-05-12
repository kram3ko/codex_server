"""Control-plane операції турну: cancel, auto-reset thread, emit_failure.

Per-turn CodexClient живе у `runner._run_locked` як context-manager; для
interrupt ззовні runner'а — Redis-registry lookup + one-shot WS до sidecar.
"""

import contextlib

import structlog

from app.db.base import SessionLocal
from app.models import EventKind
from app.services.codex import turn_registry
from app.services.codex.runner import quarantine_thread
from app.services.events.default import event_service
from app.services.sessions.store import (
    ChatSession,
    cancel_session_turn,
)

log = structlog.get_logger(__name__)


async def cancel_turn(session: ChatSession) -> bool:
    """Stop button — interrupt running Codex turn + cancel local task."""
    record = await turn_registry.get(session.db_chat_id)
    if record is not None:
        with contextlib.suppress(Exception):
            await turn_registry.send_interrupt(record)
    if not await cancel_session_turn(session):
        return False
    async with SessionLocal() as db:
        await event_service.emit(
            db,
            EventKind.TURN_INTERRUPTED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
        )
        await db.commit()
    return True


async def auto_reset_thread(session: ChatSession) -> None:
    """Idle-timeout → quarantine current thread. Наступний turn натомість
    відкриє свіжий thread (orphan tool_call resume вішає sidecar, codex#14824)."""
    record = await turn_registry.get(session.db_chat_id)
    broken_id = record.thread_id if record else None
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
    payload = {
        k: v
        for k, v in (
            ("code", code),
            ("detail", detail),
            ("exc_type", exc_type),
        )
        if v
    }
    async with SessionLocal() as db:
        await event_service.emit(
            db,
            EventKind.TURN_FAILED,
            chat_id=session.db_chat_id,
            user_id=session.db_user_id,
            payload=payload or None,
        )
        await db.commit()
