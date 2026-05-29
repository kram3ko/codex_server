"""Per-user rate limit на RunTurn (захист від badactor у guest pool).

Дві межі:
  - `active` — concurrent кількість живих турнів (одночасно)
  - `hourly` — sliding-window лічильник турнів за останні 3600с

Чотири категорії юзерів:
  - admin (UserRole.ADMIN)             → unlimited / unlimited
  - web user (USER + email)            → 2 / 30
  - tg guest (USER + tg_user_id only)  → 3 / 10

Atomicity: reserve_turn виконується через Lua-script (EVAL) — Redis крутить
script у single-threaded loop, тому ZREMRANGEBYSCORE → ZCARD → INCR → ZADD
неподільні, без race window між check і write.

Redis-падіння намірено НЕ глушимо: RedisError пройде через caller, ChatRPC
поверне INTERNAL_ERROR і Bugsink побачить root cause.
"""

import asyncio
import time
from dataclasses import dataclass

from app.models import User, UserRole
from app.services.cache.default import cache

_ACTIVE_PREFIX = "ratelimit:active:"
_HOURLY_PREFIX = "ratelimit:hourly:"
_ACTIVE_TTL_S = 600
_HOURLY_WINDOW_S = 3600

# Atomic check+incr. Returns {status, count}:
#   status = "ok" | "hourly" | "active"; count = limit-triggering value (debug-only).
_RESERVE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[2])
local hourly_count = redis.call('ZCARD', KEYS[1])
if tonumber(hourly_count) >= tonumber(ARGV[3]) then
    return {'hourly', hourly_count}
end
local active = redis.call('INCR', KEYS[2])
if tonumber(active) == 1 then
    redis.call('EXPIRE', KEYS[2], ARGV[5])
end
if tonumber(active) > tonumber(ARGV[4]) then
    redis.call('DECR', KEYS[2])
    return {'active', active - 1}
end
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[6])
return {'ok', 0}
"""


@dataclass(frozen=True, slots=True)
class _Limits:
    active: int | None
    hourly: int | None


_LIMITS: dict[str, _Limits] = {
    "admin": _Limits(None, None),
    "web_user": _Limits(2, 30),
    "tg_guest": _Limits(3, 10),
}


class RateLimited(Exception):
    """User перевищив дозволений ліміт."""

    def __init__(self, kind: str, scope: str, limit: int) -> None:
        super().__init__(f"{scope} limit reached: {limit} (user-kind={kind})")
        self.kind = kind
        self.scope = scope
        self.limit = limit


def _user_kind(user: User) -> str:
    if user.role == UserRole.ADMIN:
        return "admin"
    if user.email:
        return "web_user"
    return "tg_guest"


def _decode(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


async def reserve_turn(user: User) -> None:
    """Atomic check+reserve через Lua. Кидає RateLimited якщо межа досягнута;
    інакше caller зобов'язаний пізніше викликати `release_turn(user)`.
    """
    kind = _user_kind(user)
    limits = _LIMITS[kind]
    if limits.active is None or limits.hourly is None:
        return  # admin — без Redis-touch

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
    if _LIMITS[_user_kind(user)].active is None:
        return
    key = _ACTIVE_PREFIX + str(user.id)
    remaining = await cache.decr(key)
    if remaining <= 0:
        await cache.delete(key)
