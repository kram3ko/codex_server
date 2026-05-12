"""Control-plane операції турну: cancel, auto-reset thread, emit failure, steer.

Окремо від `runner.py` — щоб TurnRunner лишався тонкою orchestration-точкою,
без логіки відновлення/перерви/трасування помилок.
"""

import structlog
from aiogram.types import Message

from app.db.base import SessionLocal
from app.models import EventKind
from app.services.cache.default import cache
from app.services.events.default import event_service
from app.services.sessions.store import (
    ChatSession,
    cancel_session_turn,
    quarantine_key,
)
from app.tg.markdown import tg_markdown
from app.tg.media import PreparedTurn
from app.tg.turn.persistence import persist_user_turn

log = structlog.get_logger(__name__)


async def cancel_turn(session: ChatSession) -> bool:
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


async def try_steer(session: ChatSession, message: Message, prepared: PreparedTurn) -> bool:
    """Append text у працюючий turn. True — caller skip'ає новий turn.
    False → fall through до нормального запуску."""
    if session.current_turn_task is None or not prepared.text:
        return False
    ok = await session.client.steer(prepared.text)
    if not ok:
        await message.answer(tg_markdown.escape("Не вдалось додати — turn уже завершився"))
        return False
    await persist_user_turn(session, prepared)
    return True


async def auto_reset_thread(session: ChatSession) -> None:
    """Idle-timeout → quarantine current thread + open fresh one. Sidecar JSONL
    може мати orphan tool_call після обриву; resume такого thread'a віснув би
    наступний turn (codex#14824)."""
    broken_id = session.client.current_thread_id
    if broken_id:
        await cache.set(quarantine_key(broken_id), "broken", ex=86400)
    await session.client.start_new_thread()
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
