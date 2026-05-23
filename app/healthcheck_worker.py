"""Compose `HEALTHCHECK` for codex-worker.

Scan per-PID heartbeat keys `worker:heartbeat:{HOSTNAME}:{pid}` written by
TaskIQ child процеси. Exit 0 коли живих ключів >= `TASKIQ_WORKERS` (всі
child процеси heartbeat-нули в межах TTL); 1 коли менше — хтось hung
або crash'нувся без exit-у. Per-PID granularity ловить кейси коли один
child процес hangs але контейнер ще не SIGKILL-ється.

Run:
    python -m app.healthcheck_worker
"""

import asyncio
import os
import sys

from redis.exceptions import RedisError

from app.services.cache.default import cache
from app.worker import worker_heartbeat_pattern

_EXPECTED_WORKERS = int(os.environ.get("TASKIQ_WORKERS", "2"))


async def _check() -> int:
    try:
        alive = 0
        async for _ in cache.scan_iter(match=worker_heartbeat_pattern()):
            alive += 1
    except RedisError:
        return 1
    return 0 if alive >= _EXPECTED_WORKERS else 1


def main() -> int:
    return asyncio.run(_check())


if __name__ == "__main__":
    sys.exit(main())
