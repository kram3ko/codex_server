"""OpenAPI описи для health endpoint."""

from typing import Any

HEALTH_SUMMARY = "Liveness probe"
HEALTH_DESCRIPTION = """
Лайвнес-чек для docker / k8s / oncall дашборду. Без RPC/proto — простий
HTTP щоб монітор міг тицнути будь-чим.

### Використання

- `docker-compose` healthcheck: `python -m app.healthcheck` (Connect-RPC варіант)
- ручна перевірка: `curl http://localhost:8088/health`

### Що НЕ перевіряється

Це лише liveness, не readiness. Postgres/Redis/Codex sidecar НЕ опитуються.
Для readiness див. RPC `HealthService.Check` (`/api/codex.v1.HealthService/Check`).
"""

HEALTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Сервіс живий",
        "content": {
            "application/json": {
                "example": {"status": "ok", "service": "codex-api"},
            },
        },
    },
}
