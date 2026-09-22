"""Per-turn driver для Codex CLI app-server.

Обгортає WS lifecycle (`connect → handshake → resume/start thread → seed
history → return live CodexClient`) у async-context manager. Caller отримує
готовий клієнт, ганяє `run_turn(...)`, exit'ить context — `close()` сам.

Це і є місце де ми фіксимо «голос з минулого» архітектурно: один WS = один
turn, leftover-ноти попереднього turn'а взагалі не існують (вони померли з
попереднім WebSocket'ом).

Caller тримає `thread_id` cache (Postgres `chats.codex_thread_id`); resume
прозоро через `initial_thread_id` + `on_thread_change` callback.
"""

import contextlib
from collections.abc import AsyncIterator

import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.db.base import SessionLocal
from app.models import MessageRole
from app.services.cache.default import cache
from app.services.chats.default import chat_service
from app.services.codex.client import CodexClient
from app.services.codex.history import messages_to_history_items
from app.services.codex.jwt import make_ws_token
from app.services.codex.sidecar import SidecarName
from app.services.codex_prefs.default import codex_prefs_service
from app.services.codex_prefs.schemas import TurnOptions
from app.services.messages.default import message_service

log = structlog.get_logger(__name__)

_HISTORY_REPLAY_LIMIT = 20
_QUARANTINE_KEY_PREFIX = "codex:thread:quarantine:"


def quarantine_key(thread_id: str) -> str:
    return f"{_QUARANTINE_KEY_PREFIX}{thread_id}"


@contextlib.asynccontextmanager
async def open_codex_turn(
    db_chat_id: int,
    *,
    user_id: int,
    is_admin: bool,
    seed_history: bool = True,
) -> AsyncIterator[CodexClient]:
    """Відкриває fresh `CodexClient` для одного turn'а.

    1. Витягує `codex_thread_id` з Postgres (якщо `CODEX_THREAD_REUSE_ENABLED`).
    2. Перевіряє Redis quarantine — broken thread'и пропускаємо.
    3. Connect + handshake + resume/start thread.
    4. Якщо thread свіжий — seed recent N messages з БД як history items
       (workaround codex#21360: `thread/resume` upstream broken).
    5. Yields живий клієнт, caller робить `run_turn(...)`.
    6. На exit — `close()` (включно з тим, що `run_turn` вилетів exception'ом).
    """
    stored_thread_id = await _load_stored_thread_id(db_chat_id)
    initial = stored_thread_id if settings.CODEX_THREAD_REUSE_ENABLED else None
    if initial is not None and await cache.get(quarantine_key(initial)):
        log.info("codex_thread_quarantined_skip", thread_id=initial)
        initial = None

    sidecar = SidecarName.ADMIN if is_admin else SidecarName.GUEST
    turn_options = await _load_turn_options(user_id, sidecar)
    client = CodexClient(
        url=settings.CODEX_CLI_URL if is_admin else settings.CODEX_CLI_GUEST_URL,
        cwd=settings.CODEX_CWD,
        approval_policy=settings.CODEX_APPROVAL_POLICY,
        sandbox=settings.CODEX_SANDBOX,
        request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
        initial_thread_id=initial,
        on_thread_change=_thread_change_callback(db_chat_id),
        model=turn_options.model,
        reasoning_effort=turn_options.reasoning_effort,
        notification_queue_max=(
            settings.CODEX_NOTIFICATION_QUEUE_MAX_ADMIN
            if is_admin
            else settings.CODEX_NOTIFICATION_QUEUE_MAX_GUEST
        ),
        auth_token=make_ws_token(sidecar),
    )
    await client.connect()
    try:
        if seed_history:
            await client.ensure_thread()
            if client.current_thread_id != stored_thread_id:
                await _seed_history(client, db_chat_id)
        yield client
    finally:
        await client.close()


async def quarantine_thread(thread_id: str | None) -> None:
    """Mark thread broken — наступне `open_codex_turn` пропустить resume."""
    if not thread_id:
        return
    try:
        await cache.set(quarantine_key(thread_id), "broken", ex=86400)
    except RedisError as exc:
        log.warning("codex_quarantine_failed", thread_id=thread_id, error=str(exc))


async def _load_turn_options(user_id: int, sidecar: SidecarName) -> TurnOptions:
    async with SessionLocal() as db:
        return await codex_prefs_service.resolve_turn_options(db, user_id, sidecar)


async def _load_stored_thread_id(db_chat_id: int) -> str | None:
    async with SessionLocal() as db:
        chat = await chat_service.get(db, db_chat_id)
        return chat.codex_thread_id if chat else None


def _thread_change_callback(db_chat_id: int):
    async def _persist(new_thread_id: str | None) -> None:
        async with SessionLocal() as db:
            await chat_service.set_codex_thread_id(db, db_chat_id, new_thread_id)
            await db.commit()

    return _persist


async def _seed_history(client: CodexClient, db_chat_id: int) -> None:
    async with SessionLocal() as db:
        recent = await message_service.list_recent(db, db_chat_id, limit=_HISTORY_REPLAY_LIMIT)
    # Якщо останнє повідомлення — user (поточний turn), не включаємо: codex
    # отримає його через `turn/start.input`.
    if recent and recent[-1].role is MessageRole.USER:
        recent = recent[:-1]
    items = messages_to_history_items(recent)
    if not items:
        return
    await client.inject_history(items)
