"""Codex rate-limits fan-out через Redis pub/sub. Event-driven: bootstrap
once на startup (baseline) + `publish_for(sidecar)` після кожного завершеного
turn-у (момент реальної трати квоти).

Pull-RPC `account/rateLimits/read` між turn-ами — waste; sidecar НЕ пушить
notification на rate-limit зміни, а змінюється квота тільки коли codex
закриває turn (`turn/completed`). Тому єдині сенсі моменти для refresh —
TURN finalization (success/error/cancel — codex рахує що встиг).
"""

import asyncio
import contextlib
from datetime import UTC, datetime

import orjson
import structlog
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.config import settings
from app.services.cache.default import cache
from app.services.codex.client import CodexClient
from app.services.codex.sidecar import SidecarName
from app.services.codex.transport import AppServerError
from app.services.codex_usage.default import codex_usage_service
from app.services.codex_usage.service import CodexUsage

log = structlog.get_logger(__name__)

_SNAPSHOT_KEY_PREFIX = "codex:usage:snapshot:"
_CHANNEL_PREFIX = "codex:usage:"
# Bootstrap snapshot живе довго: rate-limit вікно primary 5h, secondary
# тиждень — навіть 1h stale на open показує адекватний baseline (актуальне
# значення придет з наступним turn-finalize через pub/sub).
_SNAPSHOT_TTL_S = 3600


def snapshot_key(sidecar: SidecarName) -> str:
    return f"{_SNAPSHOT_KEY_PREFIX}{sidecar}"


def channel_for(sidecar: SidecarName) -> str:
    return f"{_CHANNEL_PREFIX}{sidecar}"


def serialize(usage: CodexUsage) -> bytes:
    return orjson.dumps(usage.model_dump(mode="json"))


def deserialize(raw: bytes | str) -> CodexUsage | None:
    """Lenient: bad payload (corrupted JSON або incompatible schema after
    deploy) повертає None → subscriber пропускає frame, не обриває stream."""
    try:
        payload = orjson.loads(raw)
        return CodexUsage.model_validate(payload)
    except (orjson.JSONDecodeError, ValidationError) as exc:
        log.warning("codex_usage_deserialize_failed", error=str(exc))
        return None


async def _fetch_once(sidecar: SidecarName) -> CodexUsage | None:
    url = (
        settings.CODEX_CLI_URL
        if sidecar is SidecarName.ADMIN
        else settings.CODEX_CLI_GUEST_URL
    )
    queue_max = (
        settings.CODEX_NOTIFICATION_QUEUE_MAX_ADMIN
        if sidecar is SidecarName.ADMIN
        else settings.CODEX_NOTIFICATION_QUEUE_MAX_GUEST
    )
    client = CodexClient(
        url=url,
        cwd=settings.CODEX_CWD,
        approval_policy=settings.CODEX_APPROVAL_POLICY,
        sandbox=settings.CODEX_SANDBOX,
        request_timeout=settings.CODEX_REQUEST_TIMEOUT_SECONDS,
        notification_queue_max=queue_max,
    )
    try:
        await client.connect()
        return await codex_usage_service.latest(client)
    except (AppServerError, OSError, TimeoutError) as exc:
        log.warning("codex_usage_fetch_failed", sidecar=sidecar, error=str(exc))
        return None
    finally:
        with contextlib.suppress(Exception):
            await client.close()


async def _publish(sidecar: SidecarName, usage: CodexUsage) -> None:
    payload = serialize(usage)
    try:
        await cache.set(snapshot_key(sidecar), payload, ex=_SNAPSHOT_TTL_S)
        await cache.publish(channel_for(sidecar), payload)
    except RedisError as exc:
        log.warning("codex_usage_publish_failed", sidecar=sidecar, error=str(exc))


async def publish_for(sidecar: SidecarName) -> None:
    """Fetch + fan-out для одного sidecar-у. Тригериться runner-ом одразу
    після `finalize_once` — момент коли codex списав tokens. Помилки sidecar/
    Redis silently logged (caller не повинен на них реагувати)."""
    usage = await _fetch_once(sidecar)
    if usage is None:
        return
    usage = usage.model_copy(update={"updated_at": datetime.now(UTC)})
    await _publish(sidecar, usage)


_bg_tasks: set[asyncio.Task[None]] = set()
_inflight: set[SidecarName] = set()
_pending: set[SidecarName] = set()


def schedule_refresh(sidecar: SidecarName) -> None:
    """Coalescing leading+trailing edge: leading запускає fetch одразу;
    повторні signals while inflight ставлять `_pending`. По завершенні
    inflight task — якщо pending був set, ще один fetch (trailing). Без
    trailing edge подія яка прийшла одразу після fetch start губиться до
    наступного finalize/manual refresh."""
    if sidecar in _inflight:
        _pending.add(sidecar)
        return
    _spawn(sidecar)


def _spawn(sidecar: SidecarName) -> None:
    _inflight.add(sidecar)

    async def _run() -> None:
        try:
            await publish_for(sidecar)
        finally:
            _inflight.discard(sidecar)
            if sidecar in _pending:
                _pending.discard(sidecar)
                _spawn(sidecar)

    task = asyncio.create_task(_run(), name=f"codex-usage-refresh:{sidecar}")
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


_DRAIN_TIMEOUT_S = 3.0


async def drain_bg_tasks() -> None:
    """Shutdown helper: дочекатися in-flight refresh tasks (інакше вони
    touch-нуть вже закритий cache/transport). На stuck sidecar — cancel після
    `_DRAIN_TIMEOUT_S` щоб не блокувати reload до `CODEX_REQUEST_TIMEOUT`."""
    if not _bg_tasks:
        return
    pending = list(_bg_tasks)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=_DRAIN_TIMEOUT_S,
        )
    except TimeoutError:
        for task in pending:
            if not task.done():
                task.cancel()
        log.warning("codex_usage_drain_timeout", pending=len(pending))


async def _bootstrap_once() -> None:
    """Один initial fetch+publish для admin+guest на startup — клієнти що
    відкрили web до першого turn-у бачать baseline без чекання."""
    for sidecar in SidecarName:
        await publish_for(sidecar)


def bootstrap_usage() -> asyncio.Task[None]:
    """Fire-and-forget startup task. Lifespan тримає reference щоб task
    не GC-нувся до завершення."""
    return asyncio.create_task(_bootstrap_once(), name="codex-usage-bootstrap")
