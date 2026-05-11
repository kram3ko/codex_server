#!/bin/sh
# Down all three compose projects. Network not touched.
set -eu

cd "$(dirname "$0")"

docker compose -p codex_watch  -f compose.watch.yml --env-file ../.env down
docker compose -p codex_server -f compose.yml       --env-file ../.env down
docker compose -p codex_codex  -f compose.codex.yml --env-file ../.env down
