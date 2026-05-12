"""Gunicorn config — env-driven.

ENV:
  GUNICORN_WORKERS         кількість воркерів (default 2)
  GUNICORN_PRELOAD         true|false (default true): preload_app — master
                           імпортує код 1 раз, fork воркерам, shared via CoW.
  GUNICORN_GRACEFUL_TIMEOUT секунд на graceful drain при shutdown (default 30).

ВАЖЛИВО: --reload (auto-restart на зміну файлу) НЕ вмикати — Codex-агент
live-править код під час роботи, auto-reload рве inflight WS/turn'и.
Перезавантажити вручну після правок — `docker exec codex-server gunicornc -c reload`.
"""

import os


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


bind = "0.0.0.0:8000"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
preload_app = _bool(os.environ.get("GUNICORN_PRELOAD", "true"))

# Heartbeat canary на blocked event-loop. Per-turn idle на Codex sidecar
# ловить app-рівень (`WEB_TURN_TIMEOUT_SECONDS` / `TG_TURN_TIMEOUT_SECONDS`).
timeout = 30

# tmpfs щоб `os.fchmod` на heartbeat-файлі не блокувався disk I/O — інакше
# ложні WORKER TIMEOUT під нагрузкою (gunicorn doc: blocking-os-fchmod).
worker_tmp_dir = "/dev/shm"

# Час на graceful drain inflight requests після SIGTERM. Docker
# `stop_grace_period` у compose.yml має бути > цього (≥35s), щоб docker
# SIGKILL не випередив наш drain.
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))


def on_starting(server):
    server.log.info(
        f"gunicorn cfg: workers={workers} preload={preload_app} "
        f"timeout={timeout}s graceful={graceful_timeout}s "
        f"tmpdir={worker_tmp_dir}"
    )
