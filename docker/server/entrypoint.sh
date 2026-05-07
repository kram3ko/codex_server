#!/usr/bin/env sh
# Стартовий скрипт codex-server контейнера:
# 1) застосовує всі pending alembic-міграції,
# 2) запускає uvicorn.
set -eu

echo "[entrypoint] running alembic upgrade head"
# asyncpg's C-extension hangs at connection-pool teardown under free-threaded
# Python 3.14 (PYTHON_GIL=0). Force GIL on for migrations only — uvicorn below
# inherits PYTHON_GIL=0 from the image ENV.
PYTHON_GIL=1 alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec "$@"
