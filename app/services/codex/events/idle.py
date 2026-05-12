"""Idle-watchdog для async-стрімів ChatEvent."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.services.codex.events.types import ChatEvent


async def iterate_with_idle_timeout(
    stream: AsyncIterator[ChatEvent],
    idle_s: float,
    *,
    on_idle: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[ChatEvent]:
    """Ресетить таймер на кожен yield. idle_s тиші між events → on_idle + TimeoutError."""
    while True:
        try:
            async with asyncio.timeout(idle_s):
                event = await anext(stream)
        except StopAsyncIteration:
            return
        except TimeoutError:
            if on_idle is not None:
                await on_idle()
            raise
        yield event
