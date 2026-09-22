"""Cross-worker control-RPC до Codex sidecar. One-shot WS connection per call.

Не state — суто mechanism для крос-воркер interrupt/steer/probe. State живе у
Postgres `turns` table (`app.services.turns`); ці helper-и читають координати
(`codex_thread_id`, `codex_turn_id`) звідти і шлють `turn/interrupt|steer`.

`note_steer`/`consume_steer` — cross-worker steer signal: owner-worker idle
handler-у потрібно знати що інший worker встиг inject text у його turn, інакше
idle-timeout міг би вбити turn посеред steer-додатку.
"""

import contextlib

import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.services.cache.default import cache
from app.services.codex.client import CodexClient
from app.services.codex.jwt import make_ws_token
from app.services.codex.sidecar import SidecarName

log = structlog.get_logger(__name__)

_STEER_KEY_PREFIX = "codex:active-steer:"
_STEER_COUNT_PREFIX = "codex:steer-count:"


def _steer_key(chat_id: int) -> str:
    return f"{_STEER_KEY_PREFIX}{chat_id}"


def _steer_count_key(turn_id: int) -> str:
    return f"{_STEER_COUNT_PREFIX}{turn_id}"


def _ttl_s() -> int:
    return int(max(settings.WEB_TURN_TIMEOUT_SECONDS, settings.TG_TURN_TIMEOUT_SECONDS)) + 60


async def note_steer(chat_id: int, codex_turn_id: str) -> None:
    """Mark cross-worker steer accepted. Owner idle-watch consumes it."""
    try:
        await cache.set(_steer_key(chat_id), codex_turn_id, ex=_ttl_s())
    except RedisError as exc:
        log.warning("codex_remote_note_steer_failed", chat_id=chat_id, error=str(exc))


async def bump_steer_count(turn_id: int) -> None:
    """INCR-counter sertвіс для stream-loop persist-boundary. Окремий від
    `note_steer` (той сигналить idle-watchdog'у keepalive). Stream-loop
    porівнює локальний `seen` з cache value і на ріст робить cut_segment."""
    try:
        key = _steer_count_key(turn_id)
        await cache.incr(key)
        await cache.expire(key, _ttl_s())
    except RedisError as exc:
        log.warning("codex_remote_bump_steer_failed", turn_id=turn_id, error=str(exc))


async def peek_steer_count(turn_id: int) -> int:
    try:
        raw = await cache.get(_steer_count_key(turn_id))
        if raw is None:
            return 0
        return int(raw)
    except (RedisError, ValueError) as exc:
        log.warning("codex_remote_peek_steer_failed", turn_id=turn_id, error=str(exc))
        return 0


async def consume_steer(chat_id: int, codex_turn_id: str | None) -> bool:
    if codex_turn_id is None:
        return False
    try:
        raw = await cache.get(_steer_key(chat_id))
        if raw is None:
            return False
        seen = raw.decode() if isinstance(raw, bytes) else raw
        if seen != codex_turn_id:
            return False
        await cache.delete(_steer_key(chat_id))
        return True
    except RedisError as exc:
        log.warning("codex_remote_consume_steer_failed", chat_id=chat_id, error=str(exc))
        return False


async def send_interrupt_turn_id(
    is_admin: bool,
    *,
    thread_id: str | None,
    turn_id: str,
) -> bool:
    """Best-effort interrupt by sidecar-reported turn id.

    Skip-and-log коли thread_id ще None (turn у STARTING, `mark_started`
    не виконався) — sidecar нічого не знає про цей turn, interrupt no-op.
    """
    if thread_id is None:
        log.info("codex_remote_interrupt_skipped_no_thread", turn_id=turn_id)
        return False
    async with one_shot_client(is_admin) as client:
        return await client.interrupt(thread_id=thread_id, turn_id=turn_id)


async def send_steer_by_ids(
    *,
    chat_id: int,
    is_admin: bool,
    thread_id: str | None,
    codex_turn_id: str,
    text: str,
) -> bool:
    """Cross-worker steer transport. На accept робить тільки `note_steer`
    (idle-watchdog keepalive). `bump_steer_count` НЕ викликається тут —
    інакше stream-loop міг би побачити counter РАНІШЕ ніж caller встигне
    persist steered USER message, і chronology у БД ламається (assistant
    partial id < user steer id). Caller сам викликає `bump_steer_count`
    ПІСЛЯ persist-у USER row — це робить race неможливим."""
    async with one_shot_client(is_admin) as client:
        accepted = await client.steer(text, turn_id=codex_turn_id, thread_id=thread_id)
    if accepted:
        await note_steer(chat_id, codex_turn_id)
    return accepted


@contextlib.asynccontextmanager
async def one_shot_client(is_admin: bool):
    sidecar = SidecarName.ADMIN if is_admin else SidecarName.GUEST
    client = CodexClient(
        url=settings.CODEX_CLI_URL if is_admin else settings.CODEX_CLI_GUEST_URL,
        cwd=settings.CODEX_CWD,
        approval_policy=settings.CODEX_APPROVAL_POLICY,
        sandbox=settings.CODEX_SANDBOX,
        request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
        notification_queue_max=(
            settings.CODEX_NOTIFICATION_QUEUE_MAX_ADMIN
            if is_admin
            else settings.CODEX_NOTIFICATION_QUEUE_MAX_GUEST
        ),
        auth_token=make_ws_token(sidecar),
    )
    await client.connect()
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:
            log.warning("codex_remote_client_close_failed")
