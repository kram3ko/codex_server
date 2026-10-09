"""rate_limit — reserve/release against a real Redis: the Lua script itself is
under test, so the fake-cache shortcut is not an option here."""

from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import User, UserRole
from app.services import rate_limit as rl
from app.services.rate_limit import service as rl_service
from app.services.runtime_settings.schemas import RuntimeSettings, TurnLimits

pytestmark = pytest.mark.integration


class _StubSettings:
    def __init__(self, value: RuntimeSettings) -> None:
        self.value = value

    async def get(self) -> RuntimeSettings:
        return self.value


@pytest.fixture
def redis_cache(monkeypatch: pytest.MonkeyPatch, redis_client: Redis) -> Redis:
    monkeypatch.setattr(rl_service, "cache", redis_client)
    monkeypatch.setattr(rl_service, "runtime_settings_service", _StubSettings(RuntimeSettings()))
    return redis_client


@pytest.fixture
def hourly_ten(monkeypatch: pytest.MonkeyPatch, redis_cache: Redis) -> None:
    stub = _StubSettings(RuntimeSettings(tg_guest=TurnLimits(active=3, hourly=10)))
    monkeypatch.setattr(rl_service, "runtime_settings_service", stub)


def _web_user(uid: int = 1) -> User:
    return User(id=uid, email="user@example.com", role=UserRole.USER)


def _tg_guest(uid: int = 2) -> User:
    return User(id=uid, tg_user_id=12345, role=UserRole.USER)


def _admin(uid: int = 3) -> User:
    return User(id=uid, email="admin@example.com", role=UserRole.ADMIN)


def _active_key(user: User) -> str:
    return f"ratelimit:active:{user.id}"


def _hourly_key(user: User) -> str:
    return f"ratelimit:hourly:{user.id}"


async def test_admin_unlimited_skips_cache(redis_cache: Redis) -> None:
    user = _admin()
    for _ in range(20):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    assert await redis_cache.keys("ratelimit:*") == []


async def test_web_user_active_limit_blocks_third_concurrent(redis_cache: Redis) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.reserve_turn(user)
    with pytest.raises(rl.RateLimited) as exc:
        await rl.reserve_turn(user)
    assert (exc.value.scope, exc.value.limit) == ("active", 2)
    assert await redis_cache.get(_active_key(user)) == "2"


async def test_active_key_gets_ttl(redis_cache: Redis) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    ttl = await redis_cache.ttl(_active_key(user))
    assert 0 < ttl <= rl_service._ACTIVE_TTL_S


async def test_release_decrements_active(redis_cache: Redis) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.reserve_turn(user)
    await rl.release_turn(user)
    await rl.reserve_turn(user)
    assert await redis_cache.get(_active_key(user)) == "2"


async def test_release_zero_deletes_key(redis_cache: Redis) -> None:
    user = _web_user()
    await rl.reserve_turn(user)
    await rl.release_turn(user)
    assert await redis_cache.exists(_active_key(user)) == 0


async def test_tg_guest_hourly_off_by_default(redis_cache: Redis) -> None:
    user = _tg_guest()
    for _ in range(20):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    assert await redis_cache.exists(_hourly_key(user)) == 0
    assert await redis_cache.exists(_active_key(user)) == 0


async def test_enabling_active_limit_mid_turn_keeps_counter_consistent(
    redis_cache: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn, що стартував при active=0, після увімкнення ліміту release-иться без
    дрейфу: counter рахує живі турни, а не ті, що резервувались під лімітом."""
    user = _tg_guest()
    monkeypatch.setattr(
        rl_service,
        "runtime_settings_service",
        _StubSettings(RuntimeSettings(tg_guest=TurnLimits(active=0, hourly=0))),
    )
    await rl.reserve_turn(user)
    monkeypatch.setattr(
        rl_service,
        "runtime_settings_service",
        _StubSettings(RuntimeSettings(tg_guest=TurnLimits(active=2, hourly=0))),
    )
    await rl.reserve_turn(user)
    with pytest.raises(rl.RateLimited):
        await rl.reserve_turn(user)
    await rl.release_turn(user)
    assert await redis_cache.get(_active_key(user)) == "1"
    await rl.reserve_turn(user)
    assert await redis_cache.get(_active_key(user)) == "2"


@pytest.mark.usefixtures("hourly_ten")
async def test_tg_guest_hourly_limit(redis_cache: Redis) -> None:
    user = _tg_guest()
    for _ in range(10):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    with pytest.raises(rl.RateLimited) as exc:
        await rl.reserve_turn(user)
    assert (exc.value.scope, exc.value.limit) == ("hourly", 10)
    assert await redis_cache.zcard(_hourly_key(user)) == 10
    assert await redis_cache.ttl(_hourly_key(user)) > 0


@pytest.mark.usefixtures("hourly_ten")
async def test_hourly_rejection_does_not_leak_active_slot(redis_cache: Redis) -> None:
    user = _tg_guest()
    for _ in range(10):
        await rl.reserve_turn(user)
        await rl.release_turn(user)
    with pytest.raises(rl.RateLimited):
        await rl.reserve_turn(user)
    assert await redis_cache.exists(_active_key(user)) == 0


@pytest.mark.usefixtures("hourly_ten")
async def test_hourly_window_trims_old_entries(redis_cache: Redis) -> None:
    user = _tg_guest()
    stale = {"old1": 1.0, "old2": 2.0, "old3": 3.0}
    await redis_cache.zadd(_hourly_key(user), stale)
    await rl.reserve_turn(user)
    assert await redis_cache.zcard(_hourly_key(user)) == 1


async def test_redis_error_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, redis_cache: Redis
) -> None:
    class _BrokenCache:
        async def eval(self, *_: Any, **__: Any) -> list:
            raise RedisError("connection refused")

    monkeypatch.setattr(rl_service, "cache", _BrokenCache())
    with pytest.raises(RedisError):
        await rl.reserve_turn(_web_user())
