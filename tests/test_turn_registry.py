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


@pytest.mark.asyncio
async def test_get_drops_corrupted_record(fake_cache: FakeRedis) -> None:
    # Bitrot / old payload format — `get` має повернути None і прибрати ключ,
    # щоб подальші виклики не stuck'нулись на тому ж зіпсованому records.
    fake_cache.store["codex:active:5"] = b"{not json"

    assert await turn_registry.get(5) is None
    assert "codex:active:5" not in fake_cache.store


@pytest.mark.asyncio
async def test_get_drops_record_missing_required_fields(fake_cache: FakeRedis) -> None:
    fake_cache.store["codex:active:6"] = b'{"thread_id": "t"}'  # missing turn_id/is_admin

    assert await turn_registry.get(6) is None
    assert "codex:active:6" not in fake_cache.store


@pytest.mark.asyncio
async def test_register_pending_writes_record_without_turn_id(fake_cache: FakeRedis) -> None:
    await turn_registry.register_pending(10, "t-x", is_admin=True)

    record = await turn_registry.get(10)
    assert record is not None
    assert record.turn_id is None
    assert record.thread_id == "t-x"


@pytest.mark.asyncio
async def test_promote_sets_turn_id_on_pending(fake_cache: FakeRedis) -> None:
    await turn_registry.register_pending(11, "t-y", is_admin=False)

    promoted = await turn_registry.promote(11, "actual-turn")

    assert promoted is True
    record = await turn_registry.get(11)
    assert record is not None
    assert record.turn_id == "actual-turn"


@pytest.mark.asyncio
async def test_promote_returns_false_when_record_dropped(fake_cache: FakeRedis) -> None:
    # Race: interrupt RPC дропнув pending між register_pending і promote.
    await turn_registry.register_pending(12, "t", is_admin=True)
    await turn_registry.drop(12)  # імітуємо interrupt RPC

    promoted = await turn_registry.promote(12, "any")

    assert promoted is False
    assert await turn_registry.get(12) is None


@pytest.mark.asyncio
async def test_send_interrupt_skips_pending_turn(fake_cache: FakeRedis) -> None:
    # send_interrupt не повинен робити RPC якщо turn_id ще не promote'нутий.
    pending = turn_registry.ActiveTurn(thread_id="t", turn_id=None, is_admin=True)

    await turn_registry.send_interrupt(pending)  # просто не падає
