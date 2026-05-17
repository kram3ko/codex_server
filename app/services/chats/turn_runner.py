"""Decoupled turn execution: background `asyncio.Task` per turn, переживає
ASGI request cancellation. RPC handlers (RunTurn/TailTurn) — обидва readers
з Redis Stream `chat:{id}:events` через `turn_stream.tail()`.

Lifecycle:
  1. RPC валідує, **synchronously** `try_register_pending` (handler-side
     pre-lock з placeholder thread_id=None), persists user message
  2. `spawn(...)` запускає background + повертає `ready` event
  3. RPC `await ready.wait()` — гарантія що WS handshake відбувся
  4. RPC `tail()`'ить stream до done/error
  5. Browser disconnect → generator cancelled у RPC, background продовжує →
     user reconnect через TailTurn піймає live state

Pre-lock у handler-і (а не тут) — закриває гонку де два concurrent RunTurn
писали б user message + spawn-или дві WS перед першим register.

Shutdown: `cancel_all()` у lifespan teardown гасить orphan-таски.
"""

import asyncio
import contextlib

import structlog

from app.models import User
from app.rpc.chat.mappers import error_event
from app.rpc.chat.stream import stream_turn
from app.services import rate_limit
from app.services.chats import turn_stream
from app.services.codex import turn_registry
from app.services.codex.error_codes import CodexErrorCode
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
) -> asyncio.Event:
    """Fire-and-forget background turn. Caller передає ownership rate-limit
    release у task — НЕ викликати release у RPC finally.

    Returns `ready` event. `ready.set()` спрацьовує коли codex WS handshake
    завершений (клієнт готовий приймати turn/start). На любому crash шляху
    `finally: ready.set()` гарантує що handler ніколи не повисне."""
    ready = asyncio.Event()
    task = asyncio.create_task(
        _run(
            chat_id, user_pk, rl_user, text, image_urls, voice_reply, client_id, is_admin, ready
        ),
        name=f"chat_turn:{chat_id}",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return ready


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
    ready: asyncio.Event,
) -> None:
    thread_id: str | None = None
    turn_id: str | None = None
    terminal_published = False
    cancelled = False
    try:
        async with open_codex_turn(chat_id, is_admin=is_admin) as client:
            ready.set()
            try:
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
                    if event.WhichOneof("kind") in ("done", "error"):
                        terminal_published = True
            finally:
                thread_id = client.current_thread_id
                turn_id = client.current_turn_id
    except asyncio.CancelledError:
        cancelled = True
        log.info("background_turn_cancelled", chat_id=chat_id)
        raise
    except Exception as exc:
        log.exception("background_turn_crashed", chat_id=chat_id)
        with contextlib.suppress(Exception):
            crash = error_event(CodexErrorCode.CODEX_ERROR, f"background crashed: {exc}")
            crash.event_id = await turn_stream.publish(chat_id, crash)
            terminal_published = True
    finally:
        ready.set()  # safety: handler ніколи не повисне на ready.wait()
        if not terminal_published and not cancelled:
            with contextlib.suppress(Exception):
                dropped = error_event(
                    CodexErrorCode.STREAM_DROPPED,
                    "background turn ended without terminal event",
                )
                dropped.event_id = await turn_stream.publish(chat_id, dropped)
        await _drop_registry_record(chat_id, thread_id, turn_id)
        await turn_stream.cleanup(chat_id)
        await rate_limit.release_turn(rl_user)


async def _drop_registry_record(
    chat_id: int, thread_id: str | None, turn_id: str | None
) -> None:
    """Background task owns registry cleanup after spawn.

    Active record: (thread_id, turn_id). If Codex never reached turn/start,
    handler-side pre-lock is still the placeholder (None, None).
    """
    dropped = False
    if thread_id is not None:
        with contextlib.suppress(Exception):
            dropped = await turn_registry.drop_if_matches(chat_id, thread_id, turn_id)
    if not dropped and turn_id is None:
        with contextlib.suppress(Exception):
            await turn_registry.drop_if_matches(chat_id, None, None)
