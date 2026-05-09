import asyncio

import pytest

from app.services.codex.events import TokenEvent, iterate_with_idle_timeout


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
