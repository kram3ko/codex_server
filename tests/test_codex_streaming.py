import asyncio

import pytest

from app.services.codex.events import (
    CodexItem,
    CodexNotif,
    TokenEvent,
    iterate_with_idle_timeout,
)
from app.services.codex.transport import Notification


async def _events():
    yield TokenEvent(delta="ok")


async def _silent():
    await asyncio.sleep(10)
    yield TokenEvent(delta="never")


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_yields_events() -> None:
    events = [event async for event in iterate_with_idle_timeout(_events(), 1.0)]

    assert events == [TokenEvent(delta="ok")]


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_calls_on_idle() -> None:
    called = False

    async def on_idle() -> None:
        nonlocal called
        called = True

    with pytest.raises(TimeoutError):
        async for _ in iterate_with_idle_timeout(_silent(), 0.001, on_idle=on_idle):
            pass

    assert called is True


@pytest.mark.asyncio
async def test_idle_timeout_resets_on_hidden_notifications() -> None:
    # Regression: codex шле `item/started{reasoning}` під час довгого
    # chain-of-thought, translator повертає None → ChatEvent stream тихий.
    # Watchdog має обгортати СИРИЙ Notification stream (всередині run_turn),
    # тож reasoning-ноти ресетують timer і turn не фалшиво-помирає на idle.
    async def reasoning_then_done():
        for _ in range(5):
            await asyncio.sleep(0.02)
            yield Notification(
                method=CodexNotif.ITEM_STARTED,
                params={"item": {"type": CodexItem.REASONING}},
                turn_id="t1",
            )
        yield Notification(
            method=CodexNotif.TURN_COMPLETED, params={"finalText": "done"}, turn_id="t1"
        )

    items = [n async for n in iterate_with_idle_timeout(reasoning_then_done(), 0.1)]

    assert len(items) == 6
    assert items[-1].method == CodexNotif.TURN_COMPLETED
