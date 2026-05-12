#!/bin/sh
# Full stack up. Order matters:
#   1) codex_codex — codex-cli sidecars must be up before codex-server boots
#      (server healthcheck probes ws://codex-cli:4500).
#   2) codex_server — app + postgres + redis + minio + web. Postgres стартує
#      тут, bugsink наступним кроком на ньому ж створює свою БД.
#   3) codex_bugsink — error tracker. Окремий проект, спільна codex_net,
#      шарить codex-postgres (своя БД `bugsink`, init-контейнер створює
#      user+db при першому старті, idempotent).
#   4) codex_watch — isolated watchdog last; on boot it sweeps any orphans
#      and starts any labeled containers not yet running.
set -eu

cd "$(dirname "$0")"

docker network inspect codex_net >/dev/null 2>&1 \
  || docker network create codex_net

docker compose -p codex_codex   -f compose.codex.yml   --env-file ../.env up -d
docker compose -p codex_server  -f compose.yml         --env-file ../.env up -d
docker compose -p codex_bugsink -f compose.bugsink.yml --env-file ../.env up -d
docker compose -p codex_watch   -f compose.watch.yml   --env-file ../.env up -d
