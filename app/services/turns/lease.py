"""Event-driven liveness signal for turn-runners.

Worker, який почав виконувати turn, тримає Redis-key `turn:lease:{id}` з
своїм `worker_id` як value і TTL = `LEASE_TTL_S`. Heartbeat-loop рефрешить
TTL, finalize звільняє ключ. Якщо worker крашне без release — TTL спливе,
Redis emit'ить keyspace `expired` event, listener finalize-ить turn у БД.

Це event-driven `reconcile_stale_turns` без поллінгу — sole source of truth
про живість конкретної turn-job, на відміну від `turns.heartbeat_at` яке
може бути освіжене steer-логікою з боку, де реальний worker уже мертвий.
"""

import os
import socket
import uuid

from redis.exceptions import RedisError

from app.services.cache.default import cache

LEASE_TTL_S = 30
LEASE_KEY_PREFIX = "turn:lease:"

# Per-process holder ID: hostname + pid + random suffix. gunicorn forks і
# TaskIQ workers ділять hostname/pid-namespace, тому suffix обов'язковий
# щоб lease ownership не плутався між sibling-процесами на одному host-і.
_PROCESS_HOLDER_ID = f"{os.getenv('HOSTNAME') or socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

_REFRESH_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Per-process registry активних lease-ів. SIGTERM-handler у `app.worker` обходить
# цей set і робить graceful release — інакше при rebuild/restart worker-а
# turn'и будуть жити з expired-TTL ще ~30с, а юзер побачить `lease_expired`
# finalize замість чистого CANCELLED.
_ACTIVE_LEASES: set[tuple[int, str]] = set()


def lease_key(turn_id: int) -> str:
    return f"{LEASE_KEY_PREFIX}{turn_id}"


def turn_id_from_key(key: str) -> int | None:
    if not key.startswith(LEASE_KEY_PREFIX):
        return None
    try:
        return int(key[len(LEASE_KEY_PREFIX) :])
    except ValueError:
        return None


def worker_id() -> str:
    return _PROCESS_HOLDER_ID


async def acquire(turn_id: int, holder: str, ttl: int = LEASE_TTL_S) -> bool:
    try:
        ok = bool(await cache.set(lease_key(turn_id), holder, ex=ttl, nx=True))
    except RedisError:
        return False
    if ok:
        _ACTIVE_LEASES.add((turn_id, holder))
    return ok


async def refresh(turn_id: int, holder: str, ttl: int = LEASE_TTL_S) -> bool:
    try:
        result = await cache.eval(_REFRESH_LUA, 1, lease_key(turn_id), holder, ttl)
    except RedisError:
        return False
    return bool(result)


async def release(turn_id: int, holder: str) -> bool:
    _ACTIVE_LEASES.discard((turn_id, holder))
    try:
        result = await cache.eval(_RELEASE_LUA, 1, lease_key(turn_id), holder)
    except RedisError:
        return False
    return bool(result)


async def release_all_local() -> int:
    """Звільняє всі lease-ключі, які тримає цей процес. Викликається з
    WORKER_SHUTDOWN handler-а, щоб graceful-restart worker-а не залишав
    зомбі-lease-ів на TTL-вікно (~30с)."""
    snapshot = list(_ACTIVE_LEASES)
    released = 0
    for turn_id, holder in snapshot:
        try:
            result = await cache.eval(_RELEASE_LUA, 1, lease_key(turn_id), holder)
        except RedisError:
            continue
        if result:
            released += 1
        _ACTIVE_LEASES.discard((turn_id, holder))
    return released


async def exists(turn_id: int) -> bool:
    try:
        return bool(await cache.exists(lease_key(turn_id)))
    except RedisError:
        return False


# Leader-election lock для singleton background-loop-ів (recovery listener,
# періодичні sweep). gunicorn forkає N worker-процесів, кожен запускає
# lifespan-startup → дублює задачі. Хто перший acquire'ить — той виконує,
# решта поллять до expire і пробують перехопити.
LEADER_TTL_S = 30
LEADER_KEY_PREFIX = "leader:"


def leader_key(name: str) -> str:
    return f"{LEADER_KEY_PREFIX}{name}"


async def acquire_leader(name: str, holder: str, ttl: int = LEADER_TTL_S) -> bool:
    try:
        return bool(await cache.set(leader_key(name), holder, ex=ttl, nx=True))
    except RedisError:
        return False


async def refresh_leader(name: str, holder: str, ttl: int = LEADER_TTL_S) -> bool:
    try:
        result = await cache.eval(_REFRESH_LUA, 1, leader_key(name), holder, ttl)
    except RedisError:
        return False
    return bool(result)


async def release_leader(name: str, holder: str) -> bool:
    try:
        result = await cache.eval(_RELEASE_LUA, 1, leader_key(name), holder)
    except RedisError:
        return False
    return bool(result)
