"""Redis-backed запис живих codex турнів для cross-worker interrupt/steer.

`gunicorn -w 2+` гарантує що `runTurn` RPC і наступний `interruptTurn` /
`steerTurn` можуть прилетіти на РІЗНІ воркери (round-robin). In-memory dict
не побачить — тому ховаємо координати turn'а у Redis. Будь-який воркер
відкриває власну WS, шле `turn/interrupt|steer` зі збереженим turn_id.

Lifecycle: `try_register_pending` (NX) → `promote_pending` (CAS) →
`drop_if_matches` (CAS). Атомарність — нативні Redis 8.4+ команди
`SET ... IFEQ` і `DELEX ... IFEQ`. TTL дублює `turn-timeout + 60s` щоб
crashed worker не лишав зомбі-запис.
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
_STEER_KEY_PREFIX = "codex:active-steer:"


def _key(chat_id: int) -> str:
    return f"{_KEY_PREFIX}{chat_id}"


def _steer_key(chat_id: int) -> str:
    return f"{_STEER_KEY_PREFIX}{chat_id}"


def _ttl_s() -> int:
    return int(max(settings.WEB_TURN_TIMEOUT_SECONDS, settings.TG_TURN_TIMEOUT_SECONDS)) + 60


@dataclass(frozen=True, slots=True)
class ActiveTurn:
    thread_id: str
    turn_id: str | None  # None = pending (turn/start ще не повернув)
    is_admin: bool


class TurnOwnershipLost(RuntimeError):
    """Expected control-flow: pending record disappeared or changed before promote."""


def _encode(turn: ActiveTurn) -> str:
    # orjson дає стабільні bytes — round-trip через `decode_responses=True`
    # гарантує бітову рівність для IFEQ-порівняння на сервері.
    return orjson.dumps(
        {"thread_id": turn.thread_id, "turn_id": turn.turn_id, "is_admin": turn.is_admin}
    ).decode("utf-8")


def _decode(raw: str | bytes) -> ActiveTurn | None:
    try:
        data = orjson.loads(raw)
        return ActiveTurn(
            thread_id=data["thread_id"],
            turn_id=data["turn_id"],
            is_admin=data["is_admin"],
        )
    except (orjson.JSONDecodeError, KeyError, TypeError):
        return None


async def try_register_pending(chat_id: int, thread_id: str, is_admin: bool) -> bool:
    """Atomic NX-register pending turn. False якщо запис вже існує — caller
    MUST cancel turn (інший воркер вже володіє цим chat'ом)."""
    payload = _encode(ActiveTurn(thread_id=thread_id, turn_id=None, is_admin=is_admin))
    try:
        result = await cache.set(_key(chat_id), payload, ex=_ttl_s(), nx=True)
    except RedisError as exc:
        log.warning("turn_registry_try_register_failed", chat_id=chat_id, error=str(exc))
        return False
    if not result:
        log.warning("registry_pending_conflict", chat_id=chat_id, thread_id=thread_id)
        return False
    return True


async def promote_pending(chat_id: int, thread_id: str, turn_id: str) -> bool:
    """CAS-promote pending → active через `SET ... IFEQ <raw>`. False якщо
    record зник або був мутований у race-window — caller MUST cancel turn."""
    try:
        raw = await cache.get(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_promote_get_failed", chat_id=chat_id, error=str(exc))
        return False
    if not raw:
        return False
    rec = _decode(raw)
    if rec is None or rec.thread_id != thread_id or rec.turn_id is not None:
        return False
    new_payload = _encode(
        ActiveTurn(thread_id=thread_id, turn_id=turn_id, is_admin=rec.is_admin)
    )
    # SET ... IFEQ <raw> EX <ttl> — server байт-порівнює поточне значення з `raw`.
    # redis-py SET response callback парсить відповідь: match → True, mismatch → None.
    try:
        result = await cache.execute_command(
            "SET", _key(chat_id), new_payload, "EX", _ttl_s(), "IFEQ", raw
        )
    except RedisError as exc:
        log.warning("turn_registry_promote_set_failed", chat_id=chat_id, error=str(exc))
        return False
    return bool(result)


async def drop_if_matches(chat_id: int, thread_id: str, turn_id: str | None) -> bool:
    """CAS-delete лише якщо record == (thread_id, turn_id). `turn_id=None` →
    match pending. False якщо record зник/змінено — caller лише логить
    `registry_cas_drop_miss`, без паніки."""
    try:
        raw = await cache.get(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_drop_get_failed", chat_id=chat_id, error=str(exc))
        return False
    if not raw:
        return False
    rec = _decode(raw)
    if rec is None or rec.thread_id != thread_id or rec.turn_id != turn_id:
        return False
    # DELEX ... IFEQ <raw> — атомарне compare-and-delete. Steer-key чистимо
    # окремим викликом (втрата steer-маркера допустима, note_steer перевизве).
    try:
        deleted = await cache.execute_command("DELEX", _key(chat_id), "IFEQ", raw)
    except RedisError as exc:
        log.warning("turn_registry_delex_failed", chat_id=chat_id, error=str(exc))
        return False
    if deleted:
        with contextlib.suppress(RedisError):
            await cache.delete(_steer_key(chat_id))
    return bool(deleted)


async def get(chat_id: int) -> ActiveTurn | None:
    try:
        raw = await cache.get(_key(chat_id))
    except RedisError as exc:
        log.warning("turn_registry_get_failed", chat_id=chat_id, error=str(exc))
        return None
    if not raw:
        return None
    record = _decode(raw)
    if record is None:
        log.warning("turn_registry_corrupted_record", chat_id=chat_id)
        with contextlib.suppress(RedisError):
            await cache.delete(_key(chat_id), _steer_key(chat_id))
        return None
    return record


async def note_steer(chat_id: int, turn_id: str) -> None:
    try:
        await cache.set(_steer_key(chat_id), turn_id, ex=_ttl_s())
    except RedisError as exc:
        log.warning("turn_registry_note_steer_failed", chat_id=chat_id, error=str(exc))


async def consume_steer(chat_id: int, turn_id: str | None) -> bool:
    if turn_id is None:
        return False
    try:
        raw = await cache.get(_steer_key(chat_id))
        if raw is None:
            return False
        seen_turn_id = raw.decode() if isinstance(raw, bytes) else raw
        if seen_turn_id != turn_id:
            return False
        await cache.delete(_steer_key(chat_id))
        return True
    except RedisError as exc:
        log.warning("turn_registry_consume_steer_failed", chat_id=chat_id, error=str(exc))
        return False


async def send_interrupt(turn: ActiveTurn) -> None:
    """Best-effort cross-worker interrupt: open one-shot WS → turn/interrupt → close.
    Pre-condition: `turn.turn_id` is set (not pending)."""
    if turn.turn_id is None:
        return
    async with _one_shot_client(turn.is_admin) as client:
        await client.interrupt(turn_id=turn.turn_id)


async def send_steer(chat_id: int, turn: ActiveTurn, text: str) -> bool:
    """Cross-worker steer: appends text у running turn без володіння його WS.
    Pre-condition: `turn.turn_id` is set."""
    if turn.turn_id is None:
        return False
    async with _one_shot_client(turn.is_admin) as client:
        accepted = await client.steer(text, turn_id=turn.turn_id, thread_id=turn.thread_id)
    if accepted:
        await note_steer(chat_id, turn.turn_id)
    return accepted


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
