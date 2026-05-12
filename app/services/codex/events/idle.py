"""Idle-watchdog для async-стрімів (generic per item-type)."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable


async def iterate_with_idle_timeout[T](
    stream: AsyncIterator[T],
    idle_s: float,
    *,
    on_idle: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[T]:
    """idle_s тиші між yield → on_idle (опційно) + TimeoutError."""
    while True:
        try:
            async with asyncio.timeout(idle_s):
                item = await anext(stream)
        except StopAsyncIteration:
            return
        except TimeoutError:
            if on_idle is not None:
                await on_idle()
            raise
        yield item
