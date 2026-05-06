#!/usr/bin/env sh
# Стартовий скрипт codex-server контейнера:
# 1) застосовує всі pending alembic-міграції,
# 2) запускає uvicorn (override-нути на --reload через docker-compose у dev).
set -eu

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec "$@"
