"""TaskIQ broker для durable turn execution.

`codex-worker` контейнер запускає `taskiq worker app.worker:broker
app.services.turns.tasks --workers 2 --ack-type when_executed`. Web container
залишається тонким — `RunTurn` RPC робить `execute_turn.kiq(turn.id)` і tail-ить
per-turn Redis stream.

Idempotency: `execute_turn` перевіряє `turn.status in TERMINAL` на старті і
return-ає no-op. Worker crash → at-least-once redelivery → safe replay.

Liveness: per-process background-loop пише `worker:heartbeat:{HOSTNAME}` у
Redis з TTL > interval. `app.healthcheck_worker` читає цей ключ для compose
healthcheck → autoheal ловить hung-worker без exit.
"""

import asyncio
import contextlib
import os

import structlog
from redis.exceptions import RedisError
from taskiq import TaskiqEvents
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.config import settings

log = structlog.get_logger(__name__)

WORKER_HEARTBEAT_KEY_PREFIX = "worker:heartbeat:"
_WORKER_HEARTBEAT_INTERVAL_S = 30
_WORKER_HEARTBEAT_TTL_S = 90

broker = RedisStreamBroker(url=settings.REDIS_URL).with_result_backend(
    RedisAsyncResultBackend(redis_url=settings.REDIS_URL)
)


def worker_heartbeat_key() -> str:
    return f"{WORKER_HEARTBEAT_KEY_PREFIX}{os.getenv('HOSTNAME', 'unknown')}:{os.getpid()}"


def worker_heartbeat_pattern() -> str:
    return f"{WORKER_HEARTBEAT_KEY_PREFIX}{os.getenv('HOSTNAME', 'unknown')}:*"


async def _worker_heartbeat_loop() -> None:
    from app.services.cache.default import cache

    key = worker_heartbeat_key()
    while True:
        try:
            await cache.set(key, "alive", ex=_WORKER_HEARTBEAT_TTL_S)
        except RedisError as exc:
            log.warning("worker_heartbeat_publish_failed", error=str(exc))
        try:
            await asyncio.sleep(_WORKER_HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            return


_heartbeat_task: asyncio.Task[None] | None = None


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_worker_heartbeat(_state: object) -> None:
    global _heartbeat_task
    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(
            _worker_heartbeat_loop(), name="worker-heartbeat"
        )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _stop_worker_heartbeat(_state: object) -> None:
    global _heartbeat_task
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _heartbeat_task
        _heartbeat_task = None
    from app.services.cache.default import cache
    from app.services.turns import lease

    # Graceful release всіх lease-ів цього процесу до того як SIGKILL прилетить
    # після drain timeout. Без цього running-turn'и завершуються як `lease_expired`
    # замість чистого re-delivery.
    released = await lease.release_all_local()
    if released:
        log.info("worker_leases_released_on_shutdown", count=released)

    with contextlib.suppress(RedisError):
        await cache.delete(worker_heartbeat_key())
