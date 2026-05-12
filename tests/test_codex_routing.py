"""Unit-тести для TurnRouter — фановт notifications по turn_id.

Перевіряє ключову інваріанту: ноти попереднього турну ніколи не дістаються
до підписника наступного турну. Це і є архітектурний фікс «прилітання
попереднього повідомлення».
"""

import asyncio

import pytest

from app.services.codex.routing import TurnRouter
from app.services.codex.transport import Notification


class FakeTransport:
    """Мінімальний stub: реєструє handler'и, дає `emit` для тестів."""

    def __init__(self) -> None:
        self._on_notification = None
        self._on_close = None

    def set_notification_handler(self, handler) -> None:
        self._on_notification = handler

    def set_close_handler(self, handler) -> None:
        self._on_close = handler

    def emit(self, note: Notification) -> None:
        assert self._on_notification is not None
        self._on_notification(note)

    def fire_close(self) -> None:
        assert self._on_close is not None
        self._on_close()


def _note(turn_id: str | None, method: str = "item/agentMessage/delta") -> Notification:
    return Notification(method=method, params={"delta": "x"}, turn_id=turn_id)


@pytest.mark.asyncio
async def test_subscribe_receives_only_own_turn_notes() -> None:
    transport = FakeTransport()
    router = TurnRouter(transport)  # type: ignore[arg-type]

    async with router.subscribe_turn("turn-A") as notes:
        transport.emit(_note("turn-A"))
        transport.emit(_note("turn-B"))  # чужий — у власний буфер
        transport.emit(_note("turn-A"))
        # Закриваємо стрім, щоб iterator завершився
        transport.fire_close()
        received = [note.turn_id async for note in notes]

    assert received == ["turn-A", "turn-A"]


@pytest.mark.asyncio
async def test_subscribe_evicts_leftover_buffers_from_prior_turns() -> None:
    # Це і є той самий баг: leftover нота з попереднього турну сидить у буфері,
    # новий subscribe не повинен її побачити.
    transport = FakeTransport()
    router = TurnRouter(transport)  # type: ignore[arg-type]

    # Стара нота сидить у буфері (попередній турн вже закінчився, ніхто на ній)
    transport.emit(_note("turn-old"))

    async with router.subscribe_turn("turn-new") as notes:
        transport.emit(_note("turn-new"))
        transport.fire_close()
        received = [note.turn_id async for note in notes]

    assert received == ["turn-new"]
    # І буфер старого турну дренутий — більше не висить
    assert "turn-old" not in router._buffers  # noqa: SLF001


@pytest.mark.asyncio
async def test_session_level_notes_dropped() -> None:
    # Ноти без turnId (initialized тощо) — translator на них None,
    # сюди ми їх не доставляємо.
    transport = FakeTransport()
    router = TurnRouter(transport)  # type: ignore[arg-type]

    transport.emit(Notification(method="initialized", params={}, turn_id=None))

    async with router.subscribe_turn("turn-A") as notes:
        transport.fire_close()
        received = [note async for note in notes]

    assert received == []


@pytest.mark.asyncio
async def test_close_signals_open_subscriptions() -> None:
    transport = FakeTransport()
    router = TurnRouter(transport)  # type: ignore[arg-type]

    async def consume() -> list[str]:
        async with router.subscribe_turn("turn-A") as notes:
            return [note.turn_id async for note in notes]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # дати task піднятись і чекати на queue
    transport.fire_close()
    result = await asyncio.wait_for(task, timeout=1.0)

    assert result == []


@pytest.mark.asyncio
async def test_notes_arriving_before_subscribe_are_buffered() -> None:
    # Race: turn/start response повертається з turn_id, але ноти для цього
    # turn'а sidecar може встигнути надіслати до того як run_turn дійде до
    # subscribe_turn. Router буферить — підписник їх отримує.
    transport = FakeTransport()
    router = TurnRouter(transport)  # type: ignore[arg-type]

    transport.emit(_note("turn-A"))
    transport.emit(_note("turn-A"))

    async with router.subscribe_turn("turn-A") as notes:
        transport.fire_close()
        received = [note.turn_id async for note in notes]

    assert received == ["turn-A", "turn-A"]
