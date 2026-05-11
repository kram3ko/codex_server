#!/bin/sh
# Push-based auto-restart for containers labeled `codex.watchdog=true`.
# Boot: sweep Compose name-conflict orphans (`<12hex>_<name>`), then start the rest.
# Docs: https://docs.docker.com/reference/cli/docker/system/events/
set -eu

LABEL="${WATCHDOG_LABEL:-codex.watchdog=true}"
DEBOUNCE="${WATCHDOG_DEBOUNCE_SECONDS:-2}"

log() { printf '[watchdog] %s\n' "$*"; }

sweep_orphans() {
    docker ps -a --format '{{.ID}} {{.Names}}' \
      | awk '$2 ~ /^[0-9a-f]{12}_/ {print $1, $2}' \
      | while read -r id name; do
            log "orphan: rm $name"
            docker rm -f "$id" >/dev/null 2>&1 || log "  rm $name failed"
        done
}

ensure_running_all() {
    docker ps -a --filter "label=${LABEL}" --format '{{.Names}} {{.State}}' \
      | while read -r name state; do
            [ -z "$name" ] && continue
            case "$state" in
                running) ;;
                *) log "boot: starting $name (was: $state)"
                   docker start "$name" >/dev/null 2>&1 || log "  start $name failed" ;;
            esac
        done
}

log "label=${LABEL} debounce=${DEBOUNCE}s"
sweep_orphans
ensure_running_all

docker events \
    --filter "label=${LABEL}" \
    --filter event=die \
    --filter event=health_status \
    --format '{{.Action}}|{{.Actor.Attributes.name}}' \
  | while IFS='|' read -r action name; do
        [ -z "$name" ] && continue
        case "$action" in
            die)
                log "$name died, restart in ${DEBOUNCE}s"
                sleep "$DEBOUNCE"
                docker start "$name" >/dev/null 2>&1 || log "  restart $name failed"
                ;;
            *unhealthy*)
                log "$name unhealthy, restart in ${DEBOUNCE}s"
                sleep "$DEBOUNCE"
                docker restart "$name" >/dev/null 2>&1 || log "  restart $name failed"
                ;;
        esac
    done
