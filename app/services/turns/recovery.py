"""Recovery — finalize orphan-turn-и. Event-driven:
- runtime: Redis keyspace `expired` event на `turn:lease:{id}` ключ →
  `lease_expired_listener` finalize-ить turn (нуль поллінгу);
- startup: `reconcile_stale_turns` sweep на випадок крашу під час якого
  lease expired без живого listener-а;
- inline: `reconcile_if_stale` для caller, що тримає `TurnRow` (fast-path)."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import structlog
from redis.exceptions import RedisError

from app.db.base import SessionLocal
from app.models import TurnStatus
from app.services.cache.default import cache
from app.services.turns import lease, locks
from app.services.turns.default import turn_service
from app.services.turns.schemas import TurnRow

log = structlog.get_logger(__name__)

# 2 хв — heartbeat 10s × 12 missed; orphan з гарантією, не false-positive.
_STALE_THRESHOLD = timedelta(minutes=2)
_KEYSPACE_EXPIRED_CHANNEL = "__keyevent@0__:expired"
_LEADER_NAME = "lease-expired-listener"
_LEADER_REFRESH_INTERVAL_S = 10.0


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


async def _finalize_lease_expired(turn_id: int) -> None:
    async with SessionLocal() as db:
        turn = await turn_service.get_by_id(db, turn_id)
        if turn is None:
            return
        if turn.status not in (TurnStatus.STARTING, TurnStatus.RUNNING):
            return
        await turn_service.finalize_once(
            db,
            turn.id,
            TurnStatus.FAILED,
            error_code="lease_expired",
            error_detail="worker lease expired without release",
        )
        await db.commit()
        await locks.release_active(turn.chat_id, turn.id)
    log.warning("turn_lease_expired_finalized", turn_id=turn_id)


async def _consume_expired_events(stop: asyncio.Event, holder: str) -> None:
    pubsub = cache.pubsub()
    await pubsub.subscribe(_KEYSPACE_EXPIRED_CHANNEL)
    log.info("lease_expired_listener_started", holder=holder)
    refresh_task = asyncio.create_task(
        _refresh_leadership(stop, holder),
        name="lease-listener-leader-refresh",
    )
    try:
        async for msg in pubsub.listen():
            if stop.is_set() or refresh_task.done():
                break
            if msg.get("type") != "message":
                continue
            key = msg.get("data")
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            if not isinstance(key, str):
                continue
            turn_id = lease.turn_id_from_key(key)
            if turn_id is None:
                continue
            try:
                await _finalize_lease_expired(turn_id)
            except Exception:
                log.exception("lease_expired_finalize_failed", turn_id=turn_id)
    finally:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await refresh_task
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(_KEYSPACE_EXPIRED_CHANNEL)
        with contextlib.suppress(Exception):
            await pubsub.aclose()


async def _wait_or_timeout(stop: asyncio.Event, seconds: float) -> bool:
    """True якщо stop set-нувся, False якщо вичерпали timeout."""
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
    except TimeoutError:
        return False
    return True


async def _refresh_leadership(stop: asyncio.Event, holder: str) -> None:
    """Тримає leader-lease свіжим поки listener активний; exit-ить якщо
    Redis втратив наш holder (тоді consume-loop завершиться по `done`)."""
    while not stop.is_set():
        if await _wait_or_timeout(stop, _LEADER_REFRESH_INTERVAL_S):
            return
        if not await lease.refresh_leader(_LEADER_NAME, holder):
            log.warning("lease_listener_leadership_lost", holder=holder)
            return


async def lease_expired_listener(stop: asyncio.Event) -> None:
    """Subscribe Redis `expired` keyspace channel → finalize lease-expired turns.

    Singleton по cluster через Redis leader-lock — gunicorn workers ділять
    одну підписку, інакше N дублюючих finalize-attempt'ів. `notify-keyspace-events Ex`
    у Redis (compose-командою) — обов'язкова умова."""
    holder = lease.worker_id()
    backoff = 1.0
    while not stop.is_set():
        if not await lease.acquire_leader(_LEADER_NAME, holder):
            if await _wait_or_timeout(stop, _LEADER_REFRESH_INTERVAL_S):
                return
            continue
        try:
            await _consume_expired_events(stop, holder)
            backoff = 1.0
        except (RedisError, OSError) as exc:
            if stop.is_set():
                break
            log.warning("lease_expired_listener_reconnect", error=str(exc), backoff_s=backoff)
            await _wait_or_timeout(stop, backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            await lease.release_leader(_LEADER_NAME, holder)
    log.info("lease_expired_listener_stopped", holder=holder)
