"""Redis-backed turn registry: NX register, CAS promote, CAS drop, cross-worker.

Cache stub'ується in-memory dict — реальний Redis не потрібен. Перевіряємо
атомарність переходів (`try_register_pending`/`promote_pending`/`drop_if_matches`),
кодування `null` для pending, payload round-trip, корректну поведінку при
mismatch у CAS-вікнах.
"""

import orjson
import pytest

from app.services.codex import turn_registry


class FakeRedis:
    """Мінімум, потрібний реджистру: SET (з NX), GET, DELETE, execute_command для
    `SET ... IFEQ` та `DELEX ... IFEQ`. Decode-responses=True у проді, тут теж
    повертаємо str — щоб байт-порівняння IFEQ працювало як у реальному Redis."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self,
        key: str,
        value: str | bytes,
        ex: int | None = None,
        nx: bool = False,
    ) -> str | None:
        del ex
        v = value.decode() if isinstance(value, bytes) else value
        if nx and key in self.store:
            return None
        self.store[key] = v
        return "OK"

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                count += 1
        return count

    async def execute_command(self, *args: object) -> str | int | None:
        cmd = str(args[0]).upper()
        if cmd == "SET":
            # SET key value EX ttl IFEQ <old>
            key = str(args[1])
            value = str(args[2])
            ifeq_val: str | None = None
            for i in range(3, len(args)):
                if str(args[i]).upper() == "IFEQ":
                    ifeq_val = str(args[i + 1])
                    break
            if ifeq_val is not None and self.store.get(key) != ifeq_val:
                return None
            self.store[key] = value
            return "OK"
        if cmd == "DELEX":
            # DELEX key IFEQ <old>
            key = str(args[1])
            ifeq_val: str | None = None
            for i in range(2, len(args)):
                if str(args[i]).upper() == "IFEQ":
                    ifeq_val = str(args[i + 1])
                    break
            current = self.store.get(key)
            if current is None:
                return 0
            if ifeq_val is not None and current != ifeq_val:
                return 0
            self.store.pop(key, None)
            return 1
        raise NotImplementedError(f"FakeRedis.execute_command: {cmd}")


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(turn_registry, "cache", redis)
    return redis


@pytest.mark.asyncio
async def test_try_register_pending_writes_record_with_null_turn_id(
    fake_cache: FakeRedis,
) -> None:
    ok = await turn_registry.try_register_pending(10, "t-x", is_admin=True)

    assert ok is True
    raw = fake_cache.store["codex:active:10"]
    data = orjson.loads(raw)
    assert data == {"thread_id": "t-x", "turn_id": None, "is_admin": True}


@pytest.mark.asyncio
async def test_try_register_pending_returns_false_on_nx_collision(
    fake_cache: FakeRedis,
) -> None:
    await turn_registry.try_register_pending(11, "t-a", is_admin=True)
    second = await turn_registry.try_register_pending(11, "t-b", is_admin=False)

    assert second is False
    # перший запис лишився цілим — не перетертий
    data = orjson.loads(fake_cache.store["codex:active:11"])
    assert data["thread_id"] == "t-a"


@pytest.mark.asyncio
async def test_promote_pending_flips_null_to_turn_id(fake_cache: FakeRedis) -> None:
    await turn_registry.try_register_pending(12, "t-y", is_admin=False)

    promoted = await turn_registry.promote_pending(12, "t-y", "actual-turn")

    assert promoted is True
    record = await turn_registry.get(12)
    assert record is not None
    assert record.turn_id == "actual-turn"
    assert record.thread_id == "t-y"


@pytest.mark.asyncio
async def test_promote_pending_returns_false_when_record_dropped(
    fake_cache: FakeRedis,
) -> None:
    # Race: interrupt RPC дропнув pending між register_pending і promote.
    await turn_registry.try_register_pending(13, "t", is_admin=True)
    await turn_registry.drop_if_matches(13, "t", None)  # interrupt path

    promoted = await turn_registry.promote_pending(13, "t", "any")

    assert promoted is False


@pytest.mark.asyncio
async def test_promote_pending_returns_false_when_thread_id_mismatch(
    fake_cache: FakeRedis,
) -> None:
    await turn_registry.try_register_pending(14, "t-real", is_admin=True)

    promoted = await turn_registry.promote_pending(14, "t-wrong", "any")

    assert promoted is False


@pytest.mark.asyncio
async def test_promote_pending_returns_false_when_already_active(
    fake_cache: FakeRedis,
) -> None:
    await turn_registry.try_register_pending(15, "t", is_admin=True)
    await turn_registry.promote_pending(15, "t", "turn-1")

    second = await turn_registry.promote_pending(15, "t", "turn-2")

    assert second is False


@pytest.mark.asyncio
async def test_drop_if_matches_removes_exact_pending(fake_cache: FakeRedis) -> None:
    await turn_registry.try_register_pending(16, "t", is_admin=False)

    dropped = await turn_registry.drop_if_matches(16, "t", None)

    assert dropped is True
    assert await turn_registry.get(16) is None


@pytest.mark.asyncio
async def test_drop_if_matches_removes_exact_active(fake_cache: FakeRedis) -> None:
    await turn_registry.try_register_pending(17, "t", is_admin=False)
    await turn_registry.promote_pending(17, "t", "u")

    dropped = await turn_registry.drop_if_matches(17, "t", "u")

    assert dropped is True
    assert await turn_registry.get(17) is None


@pytest.mark.asyncio
async def test_drop_if_matches_returns_false_on_thread_mismatch(
    fake_cache: FakeRedis,
) -> None:
    await turn_registry.try_register_pending(18, "t-real", is_admin=False)

    dropped = await turn_registry.drop_if_matches(18, "t-wrong", None)

    assert dropped is False
    assert await turn_registry.get(18) is not None  # запис вижив


@pytest.mark.asyncio
async def test_drop_if_matches_returns_false_on_turn_id_mismatch(
    fake_cache: FakeRedis,
) -> None:
    await turn_registry.try_register_pending(19, "t", is_admin=False)
    await turn_registry.promote_pending(19, "t", "u-real")

    dropped = await turn_registry.drop_if_matches(19, "t", "u-stale")

    assert dropped is False
    assert (await turn_registry.get(19)).turn_id == "u-real"


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(fake_cache: FakeRedis) -> None:
    assert await turn_registry.get(999) is None


@pytest.mark.asyncio
async def test_get_drops_corrupted_record(fake_cache: FakeRedis) -> None:
    fake_cache.store["codex:active:5"] = "{not json"

    assert await turn_registry.get(5) is None
    assert "codex:active:5" not in fake_cache.store


@pytest.mark.asyncio
async def test_get_drops_record_missing_required_fields(fake_cache: FakeRedis) -> None:
    fake_cache.store["codex:active:6"] = '{"thread_id": "t"}'

    assert await turn_registry.get(6) is None
    assert "codex:active:6" not in fake_cache.store


@pytest.mark.asyncio
async def test_send_interrupt_skips_pending_turn(fake_cache: FakeRedis) -> None:
    # send_interrupt не повинен робити RPC якщо turn_id ще не promote'нутий.
    pending = turn_registry.ActiveTurn(thread_id="t", turn_id=None, is_admin=True)

    await turn_registry.send_interrupt(pending)  # просто не падає
