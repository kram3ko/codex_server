"""Per-user rate limit на RunTurn (захист від badactor у guest pool).

Дві межі:
  - `active` — concurrent кількість живих турнів (одночасно)
  - `hourly` — sliding-window лічильник турнів за останні 3600с

Чотири категорії юзерів:
  - admin (UserRole.ADMIN)             → unlimited / unlimited
  - web user (USER + email)            → 2 / 30
  - tg guest (USER + tg_user_id only)  → 1 / 10

Зберігання:
  - `ratelimit:active:{user_id}` — INT counter з safety TTL = 600s
  - `ratelimit:hourly:{user_id}` — sorted-set (ZADD timestamps), trim'имо
    `ZREMRANGEBYSCORE 0 (now-3600)`, count через `ZCARD`

Redis-падіння намірено НЕ глушимо: RedisError пройде через caller, ChatRPC
поверне INTERNAL_ERROR і Bugsink побачить root cause. Альтернатива (fail-open
з log.warning) ховала би real outage під шумом.
"""

import time
from dataclasses import dataclass

from app.models import User, UserRole
from app.services.cache.default import cache

_ACTIVE_PREFIX = "ratelimit:active:"
_HOURLY_PREFIX = "ratelimit:hourly:"
_ACTIVE_TTL_S = 600  # max турн ≈ 5хв; буфер на crash recovery
_HOURLY_WINDOW_S = 3600


@dataclass(frozen=True, slots=True)
class _Limits:
    active: int | None
    hourly: int | None


_LIMITS: dict[str, _Limits] = {
    "admin": _Limits(None, None),
    "web_user": _Limits(2, 30),
    "tg_guest": _Limits(1, 10),
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


async def reserve_turn(user: User) -> None:
    """Перевіряє ліміти і резервує active+hourly слот.

    Кидає RateLimited якщо перевищено; інакше caller зобов'язаний пізніше
    викликати `release_turn(user)` у finally.
    """
    limits = _LIMITS[_user_kind(user)]
    if limits.active is None and limits.hourly is None:
        return

    active_key = _ACTIVE_PREFIX + str(user.id)
    hourly_key = _HOURLY_PREFIX + str(user.id)
    now = time.time()

    if limits.hourly is not None:
        await cache.zremrangebyscore(hourly_key, 0, now - _HOURLY_WINDOW_S)
        count = await cache.zcard(hourly_key)
        if count >= limits.hourly:
            raise RateLimited(_user_kind(user), "hourly", limits.hourly)

    if limits.active is not None:
        active = await cache.incr(active_key)
        if active == 1:
            await cache.expire(active_key, _ACTIVE_TTL_S)
        if active > limits.active:
            # Rollback свого власного incr — щоб лічильник не залип.
            await cache.decr(active_key)
            raise RateLimited(_user_kind(user), "active", limits.active)

    if limits.hourly is not None:
        await cache.zadd(hourly_key, {str(now): now})
        await cache.expire(hourly_key, _HOURLY_WINDOW_S * 2)


async def release_turn(user: User) -> None:
    """DECR active counter. Викликати у finally після RunTurn, навіть на error."""
    if _LIMITS[_user_kind(user)].active is None:
        return
    key = _ACTIVE_PREFIX + str(user.id)
    remaining = await cache.decr(key)
    if remaining <= 0:
        await cache.delete(key)
