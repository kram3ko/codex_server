"""Композит з кількох Connect ASGI apps на один mount-point.

Connect-Python не дає combine-helper. Кожен `*ServiceASGIApplication`
очікує бачити шлях саме `/codex.v1.<Service>/<Method>`. Тому на FastAPI
side робимо `app.mount('/api', connect_router)` і не вкладаємо Mount,
а тут самостійно префікс-матчимо за `service.path`.
"""

from collections.abc import Iterable
from typing import Any

from connectrpc.server import ConnectASGIApplication


class ConnectRouter:
    """Мінімальний ASGI app: dispatch по prefix `service.path`."""

    def __init__(self, services: Iterable[ConnectASGIApplication[Any]]) -> None:
        self._services = list(services)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "lifespan"):
            return

        if scope["type"] == "lifespan":
            # Прокинути lifespan-події у кожен сервіс (connect ASGI це підтримує).
            for service in self._services:
                await service(scope, receive, send)
            return

        path: str = scope["path"]
        root: str = scope.get("root_path") or ""
        relative = path.removeprefix(root) if root and path.startswith(root) else path

        for service in self._services:
            if relative == service.path or relative.startswith(service.path + "/"):
                await service(scope, receive, send)
                return

        await send({
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({"type": "http.response.body", "body": b"not found"})
