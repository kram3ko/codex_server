"""Connect-RPC healthcheck для docker `HEALTHCHECK` директиви.

Дзвонить наш `codex.v1.HealthService.Check` через згенерований
`HealthServiceClientSync`. Exit 0 коли SERVING, 1 — інакше.

Запуск:
    python -m app.healthcheck

ENV:
    HEALTHCHECK_URL          default "http://127.0.0.1:8000/api"
    HEALTHCHECK_TIMEOUT_MS   default "3000"
"""

import os
import sys

from app.grpc_generated.codex.v1 import common_pb2
from app.grpc_generated.codex.v1.common_connect import HealthServiceClientSync


def main() -> int:
    address = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/api")
    timeout_ms = int(os.environ.get("HEALTHCHECK_TIMEOUT_MS", "3000"))

    client = HealthServiceClientSync(address=address, timeout_ms=timeout_ms)
    try:
        response = client.check(common_pb2.HealthCheckRequest())
    except Exception as exc:  # noqa: BLE001 — healthcheck must never raise
        print(f"healthcheck error: {exc!r}", file=sys.stderr)
        return 1

    if response.status != common_pb2.HealthCheckResponse.SERVING:
        status_name = common_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
        print(f"not serving: {status_name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
