"""Redis-backed запис живих codex турнів для cross-worker interrupt/steer.

`gunicorn -w 2+` гарантує що `runTurn` RPC і наступний `interruptTurn` /
`steerTurn` можуть прилетіти на РІЗНІ воркери (round-robin). In-memory dict
не побачить — тому ховаємо координати turn'а у Redis. Будь-який воркер
відкриває власну WS, шле `turn/interrupt|steer` зі збереженим turn_id.

Запис: `runTurn` після `turn/start` → `register`. На exit (success/error/cancel)
→ `drop`. TTL дублюється на час turn-timeout щоб crashed worker не лишив зомбі.
"""

import contextlib
from dataclasses import dataclass

import orjson
import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.services.cache.default import cache
from app.services.codex.client import CodexClient

log = structlog.get_logger(__name__)

_KEY_PREFIX = "codex:active:"


def _key(chat_id: int) -> str:
    return f"{_KEY_PREFIX}{chat_id}"


@dataclass(frozen=True, slots=True)
class ActiveTurn:
    thread_id: str
    turn_id: str
    is_admin: bool


async def register(chat_id: int, turn: ActiveTurn) -> None:
    payload = orjson.dumps(
        {"thread_id": turn.thread_id, "turn_id": turn.turn_id, "is_admin": turn.is_admin}
    )
    try:
        await cache.set(_key(chat_id), payload, ex=int(settings.WEB_TURN_TIMEOUT_SECONDS) + 60)
    except RedisError as exc:
        log.warning("turn_registry_register_failed", chat_id=chat_id, error=str(exc))


async def get(chat_id: int) -> ActiveTurn | None:
    try:
        raw = await cache.get(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_get_failed", chat_id=chat_id, error=str(exc))
        return None
    if not raw:
        return None
    data = orjson.loads(raw)
    return ActiveTurn(
        thread_id=data["thread_id"], turn_id=data["turn_id"], is_admin=data["is_admin"]
    )


async def drop(chat_id: int) -> None:
    try:
        await cache.delete(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_drop_failed", chat_id=chat_id, error=str(exc))


async def send_interrupt(turn: ActiveTurn) -> None:
    """Best-effort cross-worker interrupt: open one-shot WS → turn/interrupt → close."""
    async with _one_shot_client(turn.is_admin) as client:
        await client.interrupt(turn_id=turn.turn_id)


async def send_steer(turn: ActiveTurn, text: str) -> bool:
    """Cross-worker steer: appends text у running turn без володіння його WS."""
    async with _one_shot_client(turn.is_admin) as client:
        return await client.steer(text, turn_id=turn.turn_id, thread_id=turn.thread_id)


@contextlib.asynccontextmanager
async def _one_shot_client(is_admin: bool):
    client = CodexClient(
        url=settings.CODEX_CLI_URL if is_admin else settings.CODEX_CLI_GUEST_URL,
        cwd=settings.CODEX_CWD,
        approval_policy=settings.CODEX_APPROVAL_POLICY,
        sandbox=settings.CODEX_SANDBOX,
        request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
    )
    await client.connect()
    try:
        yield client
    finally:
        await client.close()
