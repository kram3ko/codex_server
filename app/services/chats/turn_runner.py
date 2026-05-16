"""Decoupled turn execution: background `asyncio.Task` per turn, переживає
ASGI request cancellation. RPC handlers (RunTurn/TailTurn) — обидва readers
з Redis Stream `chat:{id}:events` через `turn_stream.tail()`.

Lifecycle:
  1. RPC валідує + reserves rate-limit + persists user message
  2. `spawn(...)` запускає background — він open'ує Codex WS, pump'ає events
     у Redis Stream і event_bus (TG), finally — cleanup + rate-limit release
  3. RPC просто `tail()`'ить stream до done/error
  4. Browser disconnect → generator cancelled у RPC, але background task
     лишається жити (separate Task) → user reconnect через TailTurn піймає
     і replay'нуті, і нові live events

Shutdown: `cancel_all()` у lifespan teardown гасить orphan-таски.
"""

import asyncio
import contextlib

import structlog

from app.models import User
from app.rpc.chat.stream import stream_turn
from app.services import rate_limit
from app.services.chats import turn_stream
from app.services.codex.runner import open_codex_turn

log = structlog.get_logger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(
    *,
    chat_id: int,
    user_pk: int,
    rl_user: User,
    text: str,
    image_urls: tuple[str, ...] = (),
    voice_reply: bool = False,
    client_id: str | None = None,
    is_admin: bool = True,
) -> asyncio.Task:
    """Fire-and-forget background turn. Caller передає ownership rate-limit
    release у task — НЕ викликати release у RPC finally."""
    task = asyncio.create_task(
        _run(chat_id, user_pk, rl_user, text, image_urls, voice_reply, client_id, is_admin),
        name=f"chat_turn:{chat_id}",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def cancel_all() -> int:
    """Lifespan teardown: cancel + await усіх orphan-турнів. Returns count."""
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    return len(tasks)


async def _run(
    chat_id: int,
    user_pk: int,
    rl_user: User,
    text: str,
    image_urls: tuple[str, ...],
    voice_reply: bool,
    client_id: str | None,
    is_admin: bool,
) -> None:
    try:
        async with open_codex_turn(chat_id, is_admin=is_admin) as client:
            async for event in stream_turn(
                client,
                text,
                chat_id,
                user_pk,
                image_urls=image_urls,
                voice_reply=voice_reply,
                client_id=client_id,
            ):
                event.event_id = await turn_stream.publish(chat_id, event)
    except asyncio.CancelledError:
        log.info("background_turn_cancelled", chat_id=chat_id)
        raise
    except Exception:
        log.exception("background_turn_crashed", chat_id=chat_id)
    finally:
        await turn_stream.cleanup(chat_id)
        await rate_limit.release_turn(rl_user)
