"""Recovery — finalize orphan-turn-и. Event-driven: startup-sweep +
inline-check у `get_active_for_chat` (через `reconcile_if_stale`). Без
periodic-loop."""

from datetime import UTC, datetime, timedelta

import structlog

from app.db.base import SessionLocal
from app.models import TurnStatus
from app.services.turns import locks
from app.services.turns.default import turn_service
from app.services.turns.schemas import TurnRow

log = structlog.get_logger(__name__)

# 2 хв — heartbeat 10s × 12 missed; orphan з гарантією, не false-positive.
_STALE_THRESHOLD = timedelta(minutes=2)


async def reconcile_stale_turns() -> int:
    async with SessionLocal() as db:
        stale = await turn_service.find_stale_active(db, _STALE_THRESHOLD)
        for turn in stale:
            await _finalize_orphan(db, turn)
        await db.commit()
    return len(stale)


async def reconcile_if_stale(turn: TurnRow) -> bool:
    """Caller-driven check. True якщо turn був stale і finalize-нутий."""
    age = datetime.now(UTC) - turn.heartbeat_at
    if age < _STALE_THRESHOLD:
        return False
    async with SessionLocal() as db:
        await _finalize_orphan(db, turn)
        await db.commit()
    return True


async def _finalize_orphan(db, turn: TurnRow) -> None:
    await turn_service.finalize_once(
        db,
        turn.id,
        TurnStatus.FAILED,
        error_code="orphan_heartbeat",
        error_detail=f"no heartbeat for >{_STALE_THRESHOLD.total_seconds()}s",
    )
    log.warning("turn_reconcile_finalized", turn_id=turn.id)
    await locks.release_active(turn.chat_id, turn.id)
