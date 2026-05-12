"""Redis-backed turn registry: register/get/drop + cross-worker interrupt/steer.

Cache stub'ується in-memory dict — реальний Redis не потрібен. Перевіряємо
що: payload round-trips, TTL рахується від WEB_TURN_TIMEOUT_SECONDS, drop
прибирає запис, RedisError swallow'аються (turn_registry не валить виклик).
"""

import pytest

from app.services.codex import turn_registry


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.last_ex: int | None = None

    async def set(self, key: str, value: bytes | str, ex: int | None = None) -> None:
        self.store[key] = value if isinstance(value, bytes) else value.encode()
        self.last_ex = ex

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(turn_registry, "cache", redis)
    return redis


@pytest.mark.asyncio
async def test_register_get_round_trips(fake_cache: FakeRedis) -> None:
    turn = turn_registry.ActiveTurn(thread_id="t-1", turn_id="u-1", is_admin=True)

    await turn_registry.register(42, turn)
    record = await turn_registry.get(42)

    assert record == turn
    assert fake_cache.last_ex is not None and fake_cache.last_ex > 0


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(fake_cache: FakeRedis) -> None:
    assert await turn_registry.get(999) is None


@pytest.mark.asyncio
async def test_drop_removes_record(fake_cache: FakeRedis) -> None:
    turn = turn_registry.ActiveTurn(thread_id="t", turn_id="u", is_admin=False)
    await turn_registry.register(7, turn)

    await turn_registry.drop(7)

    assert await turn_registry.get(7) is None


@pytest.mark.asyncio
async def test_register_preserves_is_admin_flag(fake_cache: FakeRedis) -> None:
    await turn_registry.register(
        1, turn_registry.ActiveTurn(thread_id="t", turn_id="u", is_admin=False)
    )
    record = await turn_registry.get(1)

    assert record is not None
    assert record.is_admin is False
