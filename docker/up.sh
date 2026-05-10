#!/bin/sh
# Запуск повного стеку: controller (codex-cli/guest) + app stack.
# Розділення проектів усуває self-recreate paradox.
set -eu

cd "$(dirname "$0")"

docker network inspect codex_net >/dev/null 2>&1 \
  || docker network create codex_net

docker compose -p codex_codex -f compose.codex.yml --env-file ../.env up -d
docker compose -p codex_server -f compose.yml --env-file ../.env up -d
