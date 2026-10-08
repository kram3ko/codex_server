"""Per-user rate limit на RunTurn (захист від badactor у guest pool).

Дві межі (значення з адмін-керованих `RuntimeSettings`, 0 = вимкнено):
  - `active` — concurrent кількість живих турнів (одночасно)
  - `hourly` — sliding-window лічильник турнів за останні 3600с

Admin — без лімітів і без Redis-touch.

Atomicity: reserve_turn виконується через Lua-script (EVAL) — Redis крутить
script у single-threaded loop, тому ZREMRANGEBYSCORE → ZCARD → INCR → ZADD
неподільні, без race window між check і write.

Redis-падіння намірено НЕ глушимо: RedisError пройде через caller, ChatRPC
поверне INTERNAL_ERROR і Bugsink побачить root cause.
"""

import asyncio
import time

from app.models import User
from app.services.cache.default import cache
from app.services.runtime_settings.default import runtime_settings_service
from app.services.runtime_settings.schemas import UserKind, user_kind

_ACTIVE_PREFIX = "ratelimit:active:"
_HOURLY_PREFIX = "ratelimit:hourly:"
_ACTIVE_TTL_S = 600
_HOURLY_WINDOW_S = 3600

# Atomic check+incr. Returns {status, count}:
#   status = "ok" | "hourly" | "active"; count = limit-triggering value (debug-only).
# Active counter INCR-иться завжди (навіть при active_limit=0): ліміт можуть
# увімкнути посеред turn-а, а release має що DECR-ити без дрейфу лічильника.
_RESERVE_LUA = """
local hourly_limit = tonumber(ARGV[3])
local active_limit = tonumber(ARGV[4])
if hourly_limit > 0 then
    redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[2])
    local hourly_count = redis.call('ZCARD', KEYS[1])
    if tonumber(hourly_count) >= hourly_limit then
        return {'hourly', hourly_count}
    end
end
local active = redis.call('INCR', KEYS[2])
if tonumber(active) == 1 then
    redis.call('EXPIRE', KEYS[2], ARGV[5])
end
if active_limit > 0 and tonumber(active) > active_limit then
    redis.call('DECR', KEYS[2])
    return {'active', active - 1}
end
if hourly_limit > 0 then
    redis.call('ZADD', KEYS[1], ARGV[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[6])
end
return {'ok', 0}
"""


class RateLimited(Exception):
    """User перевищив дозволений ліміт."""

    def __init__(self, kind: UserKind, scope: str, limit: int) -> None:
        super().__init__(f"{scope} limit reached: {limit} (user-kind={kind})")
        self.kind = kind
        self.scope = scope
        self.limit = limit


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def reserve_turn(user: User) -> None:
    """Atomic check+reserve через Lua. Кидає RateLimited якщо межа досягнута;
    інакше caller зобов'язаний пізніше викликати `release_turn(user)`.
    """
    kind = user_kind(user)
    if kind is UserKind.ADMIN:
        return
    limits = (await runtime_settings_service.get()).turn_limits(kind)

    active_key = _ACTIVE_PREFIX + str(user.id)
    hourly_key = _HOURLY_PREFIX + str(user.id)
    now = time.time()

    # redis-py типізує `eval` як `str | Awaitable[str]` (sync/async overload);
    # на async-client це завжди awaitable, але pyright не narrowить.
    result = cache.eval(
        _RESERVE_LUA,
        2,
        hourly_key,
        active_key,
        str(now),
        str(now - _HOURLY_WINDOW_S),
        str(limits.hourly),
        str(limits.active),
        str(_ACTIVE_TTL_S),
        str(_HOURLY_WINDOW_S * 2),
    )
    if asyncio.iscoroutine(result):
        result = await result
    if not isinstance(result, list):
        raise RuntimeError(f"unexpected Lua return shape: {type(result).__name__}")
    status = _decode(result[0])
    if status == "hourly":
        raise RateLimited(kind, "hourly", limits.hourly)
    if status == "active":
        raise RateLimited(kind, "active", limits.active)


async def release_turn(user: User) -> None:
    """DECR active counter. Викликати у finally після RunTurn, навіть на error."""
    if user_kind(user) is UserKind.ADMIN:
        return
    key = _ACTIVE_PREFIX + str(user.id)
    remaining = await cache.decr(key)
    if remaining <= 0:
        await cache.delete(key)
