"""rate_limit — per-user reserve/release без реального Redis (in-memory fake)."""

from typing import Any

import pytest
from redis.exceptions import RedisError

from app.models import User, UserRole
from app.services import rate_limit as rl
from app.services.rate_limit import service as rl_service


class _FakeCache:
    """Async Redis stub: decr/delete + eval (replicates Lua reserve script)."""

    def __init__(self) -> None:
        self.ints: dict[str, int] = {}
        self.zsets: dict[str, list[tuple[float, str]]] = {}

    async def decr(self, key: str) -> int:
        self.ints[key] = self.ints.get(key, 0) - 1
        return self.ints[key]

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in self.ints:
                del self.ints[k]
                deleted += 1
            if k in self.zsets:
                del self.zsets[k]
                deleted += 1
        return deleted

    async def eval(self, _script: str, _numkeys: int, *args: str) -> list:
        # Replicates _RESERVE_LUA atomically (single-threaded loop у Redis →
        # тут просто послідовно).
        hourly_key, active_key = args[0], args[1]
        now = float(args[2])
        window_start = float(args[3])
        hourly_limit = int(args[4])
        active_limit = int(args[5])
        # ZREMRANGEBYSCORE
        items = self.zsets.get(hourly_key, [])
        self.zsets[hourly_key] = [(s, m) for (s, m) in items if s > window_start]
        # ZCARD check
        count = len(self.zsets[hourly_key])
        if count >= hourly_limit:
            return [b"hourly", count]
        # INCR + active check
        self.ints[active_key] = self.ints.get(active_key, 0) + 1
        active = self.ints[active_key]
        if active > active_limit:
            self.ints[active_key] = active - 1
            return [b"active", active - 1]
        # ZADD
        self.zsets[hourly_key].append((now, str(now)))
        return [b"ok", 0]


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> _FakeCache:
    cache = _FakeCache()
    monkeypatch.setattr(rl_service, "cache", cache)
    return cache


def _web_user(uid: int = 1) -> User:
    return User(id=uid, email="user@example.com", role=UserRole.USER)


def _tg_guest(uid: int = 2) -> User:
    return User(id=uid, tg_user_id=12345, role=UserRole.USER)


def _admin(uid: int = 3) -> User:
    return User(id=uid, email="admin@example.com", role=UserRole.ADMIN)


async def test_admin_unlimited_skips_cache(fake_cache: _FakeCache) -> None:
    """Admin bypass'ить ліміти — жодних touchpoint'ів у Redis."""
    user = _admin()
    for _ in range(100):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    assert fake_cache.ints == {}
    assert fake_cache.zsets == {}


async def test_web_user_active_limit_blocks_third_concurrent(fake_cache: _FakeCache) -> None:
    """web_user: 2 active. Третій concurrent reserve кидає RateLimited(active)."""
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.reserve_turn(user)
    with pytest.raises(rl.RateLimited) as exc:
        await rl.reserve_turn(user)
    assert exc.value.scope == "active"
    assert exc.value.limit == 2
    # Rollback own incr — counter лишається на 2, не 3.
    assert fake_cache.ints[f"ratelimit:active:{user.id}"] == 2


async def test_release_decrements_active(fake_cache: _FakeCache) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.reserve_turn(user)
    await rl.release_turn(user)
    # Один турн зайнятий — третій тепер пройде.
    await rl.reserve_turn(user)
    assert fake_cache.ints[f"ratelimit:active:{user.id}"] == 2


async def test_release_zero_deletes_key(fake_cache: _FakeCache) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.release_turn(user)
    assert f"ratelimit:active:{user.id}" not in fake_cache.ints


async def test_tg_guest_hourly_limit(fake_cache: _FakeCache) -> None:
    """tg_guest: 10 turns / hour. 11-й кидає RateLimited(hourly)."""
    user = _tg_guest()
    for _ in range(10):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    with pytest.raises(rl.RateLimited) as exc:
        await rl.reserve_turn(user)
    assert exc.value.scope == "hourly"
    assert exc.value.limit == 10


async def test_hourly_window_trims_old_entries(fake_cache: _FakeCache) -> None:
    """ZREMRANGEBYSCORE 0 (now-3600) — старші > 1h entries не рахуються."""
    user = _tg_guest()
    key = f"ratelimit:hourly:{user.id}"
    # Старі entries за межами вікна (negative score = -1 → завжди < now-3600).
    fake_cache.zsets[key] = [(-1.0, "old1"), (-1.0, "old2"), (-1.0, "old3")]
    await rl.reserve_turn(user)
    # Старі мають вилетіти; лишається 1 свіжий.
    assert len(fake_cache.zsets[key]) == 1


async def test_redis_error_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-loud: RedisError підіймається у caller, не глушиться у no-op."""

    class _BrokenCache:
        async def eval(self, *_: Any, **__: Any) -> list:
            raise RedisError("connection refused")

    monkeypatch.setattr(rl_service, "cache", _BrokenCache())
    with pytest.raises(RedisError):
        await rl.reserve_turn(_web_user())
