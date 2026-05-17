"""Redis locks для turn ownership. CAS-семантика через `SET ... IFEQ` /
`DELEX ... IFEQ` — heartbeat/release тільки коли value == own turn_id.
Heartbeat fail = ownership lost; runner MUST finalize FAILED."""

import contextlib
from collections.abc import AsyncIterator
from enum import StrEnum

import structlog
from redis.exceptions import RedisError

from app.services.cache.default import cache

log = structlog.get_logger(__name__)

# 2x heartbeat interval (10-15s з runner-а) щоб пережити event-loop drift.
_LOCK_TTL_S = 30


class LockAcquireOutcome(StrEnum):
    ACQUIRED = "acquired"
    REDELIVERY = "redelivery"  # same turn_id уже тримає active lock
    ACTIVE_BUSY = "active_busy"  # інший turn у тому ж chat


def _active_key(chat_id: int) -> str:
    return f"turn:active:{chat_id}"


def _slot_key(sidecar: str) -> str:
    return f"codex:slot:{sidecar}:0"


async def try_acquire_active(chat_id: int, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        result = await cache.set(_active_key(chat_id), payload, ex=_LOCK_TTL_S, nx=True)
    except RedisError as exc:
        log.warning("turn_active_acquire_failed", chat_id=chat_id, error=str(exc))
        return False
    return bool(result)


async def active_lock_value(chat_id: int) -> str | None:
    """Read current `turn:active:{chat_id}` holder. None якщо вільно."""
    try:
        raw = await cache.get(_active_key(chat_id))
    except RedisError:
        return None
    if raw is None:
        return None
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore")


async def heartbeat_active(chat_id: int, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        result = await cache.execute_command(
            "SET", _active_key(chat_id), payload, "EX", _LOCK_TTL_S, "IFEQ", payload
        )
    except RedisError as exc:
        log.warning("turn_active_heartbeat_failed", chat_id=chat_id, error=str(exc))
        return False
    return bool(result)


async def release_active(chat_id: int, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        deleted = await cache.execute_command("DELEX", _active_key(chat_id), "IFEQ", payload)
    except RedisError as exc:
        log.warning("turn_active_release_failed", chat_id=chat_id, error=str(exc))
        return False
    return bool(deleted)


async def try_acquire_slot(sidecar: str, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        result = await cache.set(_slot_key(sidecar), payload, ex=_LOCK_TTL_S, nx=True)
    except RedisError as exc:
        log.warning("codex_slot_acquire_failed", sidecar=sidecar, error=str(exc))
        return False
    return bool(result)


async def heartbeat_slot(sidecar: str, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        result = await cache.execute_command(
            "SET", _slot_key(sidecar), payload, "EX", _LOCK_TTL_S, "IFEQ", payload
        )
    except RedisError as exc:
        log.warning("codex_slot_heartbeat_failed", sidecar=sidecar, error=str(exc))
        return False
    return bool(result)


async def release_slot(sidecar: str, turn_id: int) -> bool:
    payload = str(turn_id)
    try:
        deleted = await cache.execute_command("DELEX", _slot_key(sidecar), "IFEQ", payload)
    except RedisError as exc:
        log.warning("codex_slot_release_failed", sidecar=sidecar, error=str(exc))
        return False
    return bool(deleted)


@contextlib.asynccontextmanager
async def hold_turn_locks(
    chat_id: int, sidecar: str, turn_id: int
) -> AsyncIterator[LockAcquireOutcome]:
    """Acquire per-chat active lock → release on exit. Sidecar slot lock
    видалено: codex-cli тримає окремий thread/WS на chat, кілька активних
    chat-ів у одному sidecar безпечні."""
    del sidecar  # legacy signature compat — рознесемо у наступному cleanup-і
    active_acquired = await try_acquire_active(chat_id, turn_id)
    if not active_acquired:
        if await active_lock_value(chat_id) == str(turn_id):
            yield LockAcquireOutcome.REDELIVERY
        else:
            yield LockAcquireOutcome.ACTIVE_BUSY
        return
    try:
        yield LockAcquireOutcome.ACQUIRED
    finally:
        await release_active(chat_id, turn_id)
