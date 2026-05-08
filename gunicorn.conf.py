"""Gunicorn config — env-driven.

ENV:
  GUNICORN_WORKERS  кількість воркерів (default 2)
  GUNICORN_PRELOAD  true|false (default true): preload_app — master імпортує
                    код 1 раз, fork воркерам, страви shared via Linux CoW.

ВАЖЛИВО: --reload (auto-restart на зміну файлу) НЕ вмикати — Codex-агент
live-править код під час роботи, auto-reload рве inflight WS/turn'и.
Для перезавантаження після правок — `docker exec codex-server gunicornc -c reload`.
"""

import os


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


bind = "0.0.0.0:8000"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
preload_app = _bool(os.environ.get("GUNICORN_PRELOAD", "true"))
timeout = 60


def on_starting(server):
    server.log.info(
        f"gunicorn cfg: workers={workers} preload={preload_app} timeout={timeout}s"
    )
