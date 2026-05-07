"""Connect handler для `codex.v1.HealthService.Check` — readiness probe.

Паралельно пінгує Postgres + Redis + Codex sidecar через `asyncio.TaskGroup`,
агрегує результат у єдиний SERVING/NOT_SERVING статус. Дзвінок жорстко
обмежений `_PROBE_TIMEOUT_S` через `asyncio.timeout` — чорний ящик для
docker healthcheck не повинен висіти.
"""

import asyncio
import contextlib
from typing import override

import structlog
import websockets
from connectrpc.request import RequestContext
from sqlalchemy import text

from app.config import settings
from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2
from app.grpc_generated.codex.v1.common_connect import HealthService as HealthProtocol
from app.services.cache.default import cache

log = structlog.get_logger(__name__)

_PROBE_TIMEOUT_S = 3.0
_SERVING = common_pb2.HealthCheckResponse.SERVING
_NOT_SERVING = common_pb2.HealthCheckResponse.NOT_SERVING


class HealthRPC(HealthProtocol):
    """Probes downstream deps; returns SERVING only when all pass."""

    @override
    async def check(
        self,
        request: common_pb2.HealthCheckRequest,
        ctx: RequestContext,
    ) -> common_pb2.HealthCheckResponse:
        del request, ctx
        results: dict[str, bool] = {}
        try:
            async with asyncio.timeout(_PROBE_TIMEOUT_S), asyncio.TaskGroup() as tg:
                pg = tg.create_task(_probe_postgres())
                rd = tg.create_task(_probe_redis())
                cx = tg.create_task(_probe_codex_sidecar())
            results = {"postgres": pg.result(), "redis": rd.result(), "codex": cx.result()}
        except* Exception as eg:  # noqa: BLE001
            for exc in eg.exceptions:
                log.warning("healthcheck_probe_failed", error=str(exc))

        all_ok = bool(results) and all(results.values())
        log.info("healthcheck_done", **results, serving=all_ok)
        return common_pb2.HealthCheckResponse(status=_SERVING if all_ok else _NOT_SERVING)


async def _probe_postgres() -> bool:
    with contextlib.suppress(Exception):
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    return False


async def _probe_redis() -> bool:
    with contextlib.suppress(Exception):
        # redis-py типізує ping() як `Awaitable[bool] | bool` (sync/async overload);
        # на async client це завжди awaitable, але pyright не narrowить.
        result = cache.ping()
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)
    return False


async def _probe_codex_sidecar() -> bool:
    """Cheap WS-handshake to confirm sidecar accepts connections."""
    with contextlib.suppress(Exception):
        async with websockets.connect(
            settings.CODEX_APP_SERVER_URL,
            open_timeout=2.0,
            close_timeout=1.0,
        ):
            pass
        return True
    return False
