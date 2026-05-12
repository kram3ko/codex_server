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
    turn_id: str | None  # None = pending (turn/start ще не повернув)
    is_admin: bool


async def register(chat_id: int, turn: ActiveTurn) -> None:
    payload = orjson.dumps(
        {"thread_id": turn.thread_id, "turn_id": turn.turn_id, "is_admin": turn.is_admin}
    )
    try:
        await cache.set(_key(chat_id), payload, ex=int(settings.WEB_TURN_TIMEOUT_SECONDS) + 60)
    except RedisError as exc:
        log.warning("turn_registry_register_failed", chat_id=chat_id, error=str(exc))


async def register_pending(chat_id: int, thread_id: str, is_admin: bool) -> None:
    """Pre-turn запис з `turn_id=None` до того як `turn/start` повернув. Закриває
    race-window: interrupt RPC у це вікно бачить pending → робить `drop`, що
    сигналізує worker'у-власнику турна в `promote()`. Без додаткового флага."""
    await register(chat_id, ActiveTurn(thread_id=thread_id, turn_id=None, is_admin=is_admin))


async def promote(chat_id: int, turn_id: str) -> bool:
    """Дописати turn_id у pending запис. Returns False якщо record зник
    (interrupt RPC drop'нув його у race-window) — caller має cancel'нути turn."""
    record = await get(chat_id)
    if record is None:
        return False
    await register(
        chat_id, ActiveTurn(thread_id=record.thread_id, turn_id=turn_id, is_admin=record.is_admin)
    )
    return True


async def get(chat_id: int) -> ActiveTurn | None:
    try:
        raw = await cache.get(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_get_failed", chat_id=chat_id, error=str(exc))
        return None
    if not raw:
        return None
    try:
        data = orjson.loads(raw)
        return ActiveTurn(
            thread_id=data["thread_id"], turn_id=data["turn_id"], is_admin=data["is_admin"]
        )
    except (orjson.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("turn_registry_corrupted_record", chat_id=chat_id, error=str(exc))
        await drop(chat_id)
        return None


async def drop(chat_id: int) -> None:
    try:
        await cache.delete(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_drop_failed", chat_id=chat_id, error=str(exc))


async def send_interrupt(turn: ActiveTurn) -> None:
    """Best-effort cross-worker interrupt: open one-shot WS → turn/interrupt → close.
    Pre-condition: `turn.turn_id` is set (not pending)."""
    if turn.turn_id is None:
        return
    async with _one_shot_client(turn.is_admin) as client:
        await client.interrupt(turn_id=turn.turn_id)


async def send_steer(turn: ActiveTurn, text: str) -> bool:
    """Cross-worker steer: appends text у running turn без володіння його WS.
    Pre-condition: `turn.turn_id` is set."""
    if turn.turn_id is None:
        return False
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
        with contextlib.suppress(Exception):
            await client.close()
