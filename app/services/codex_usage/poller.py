"""Codex rate-limits fan-out через Redis pub/sub.

`account/rateLimits/updated` містить готовий snapshot для live updates.
`account/rateLimits/read` лишається для bootstrap/manual refresh.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import orjson
import structlog
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.config import settings
from app.services.cache.default import cache
from app.services.codex.client import CodexClient
from app.services.codex.jwt import make_ws_token
from app.services.codex.sidecar import SidecarName
from app.services.codex.transport import AppServerError
from app.services.codex_usage.default import codex_usage_service
from app.services.codex_usage.service import CodexUsage

log = structlog.get_logger(__name__)

_SNAPSHOT_KEY_PREFIX = "codex:usage:snapshot:"
_CHANNEL_PREFIX = "codex:usage:"
# Bootstrap snapshot живе довго: rate-limit вікно primary 5h, secondary
# тиждень — навіть 1h stale на open показує адекватний baseline (актуальне
# значення придет з наступним `account/rateLimits/updated` через pub/sub).
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
    url = settings.CODEX_CLI_URL if sidecar is SidecarName.ADMIN else settings.CODEX_CLI_GUEST_URL
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
        auth_token=make_ws_token(sidecar),
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


async def publish_rate_limits(sidecar: SidecarName, snapshot: dict[str, Any]) -> None:
    try:
        usage = codex_usage_service.from_rate_limits(snapshot)
    except ValidationError as exc:
        log.warning("codex_usage_notification_invalid", sidecar=sidecar, error=str(exc))
        return
    await _publish(sidecar, usage)


async def publish_for(sidecar: SidecarName) -> None:
    usage = await _fetch_once(sidecar)
    if usage is None:
        return
    usage = usage.model_copy(update={"updated_at": datetime.now(UTC)})
    await _publish(sidecar, usage)


async def _bootstrap_once() -> None:
    for sidecar in SidecarName:
        await publish_for(sidecar)


def bootstrap_usage() -> asyncio.Task[None]:
    """Fire-and-forget startup task. Lifespan тримає reference щоб task
    не GC-нувся до завершення."""
    return asyncio.create_task(_bootstrap_once(), name="codex-usage-bootstrap")
