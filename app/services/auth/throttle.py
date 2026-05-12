"""Failed-login counter у Redis для brute-force захисту.

Key: `auth:bf:{ip}` — incr на кожну невдалу спробу, TTL sliding window.
Перевищили `_MAX_ATTEMPTS` за `_WINDOW_S` → блокуємо із `_LOCKOUT_S`.
Успіх — `clear(ip)`.

Solo-масштаб: бачимо реальний effect тільки якщо хтось знайде endpoint
ззовні tailnet'у. Без Redis — `Throttle.check` no-op (cache disabled).
"""

import structlog
from redis.exceptions import RedisError

from app.services.cache.default import cache

log = structlog.get_logger(__name__)

_KEY_PREFIX = "auth:bf:"
_MAX_ATTEMPTS = 5
_WINDOW_S = 15 * 60
_LOCKOUT_S = 15 * 60


class LoginThrottled(Exception):
    """IP перевищив бар'єр невдалих логінів."""


async def check(ip: str | None) -> None:
    """Кидає LoginThrottled якщо IP заблокований. Викликати ПЕРЕД verify."""
    if not ip:
        return
    try:
        attempts = await cache.get(_KEY_PREFIX + ip)
    except RedisError as exc:
        log.warning("auth_throttle_check_failed", error=str(exc))
        return
    if attempts is not None and int(attempts) >= _MAX_ATTEMPTS:
        raise LoginThrottled(f"too many failed attempts; try again in {_LOCKOUT_S // 60}min")


async def register_failure(ip: str | None) -> None:
    if not ip:
        return
    key = _KEY_PREFIX + ip
    try:
        count = await cache.incr(key)
        if count == 1:
            await cache.expire(key, _WINDOW_S)
        elif count >= _MAX_ATTEMPTS:
            await cache.expire(key, _LOCKOUT_S)
    except RedisError as exc:
        log.warning("auth_throttle_incr_failed", error=str(exc))


async def clear(ip: str | None) -> None:
    if not ip:
        return
    try:
        await cache.delete(_KEY_PREFIX + ip)
    except RedisError as exc:
        log.warning("auth_throttle_clear_failed", error=str(exc))
