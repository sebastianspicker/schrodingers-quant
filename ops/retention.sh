#!/bin/sh
# ops/retention.sh — bound disk usage on the VPS: prune dangling Docker
# images and print journald retention guidance. Safe by default: prints what
# it would do and changes nothing unless --apply is given.
#
# Usage: ops/retention.sh [--apply]
#
# OHLCV cache pruning under user_data/data was deliberately removed: the bot
# container runs Freqtrade in trade mode only and does not mount or write to
# user_data/data, so there is nothing there for this script to prune.
set -eu

apply=false

usage() {
    printf 'Usage: %s [--apply]\n' "$0" >&2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --apply)
            apply=true
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

if [ "$apply" = false ]; then
    log "dry-run (default): pass --apply to actually delete/prune anything"
fi

if [ "$apply" = true ]; then
    log "pruning dangling Docker images"
    docker image prune -f --filter dangling=true
else
    log "[dry-run] would run: docker image prune -f --filter dangling=true"
fi

log "journald guidance (not applied by this script):"
log "  journalctl --disk-usage                 # check current journal size"
log "  sudo journalctl --vacuum-time=30d        # or --vacuum-size=500M"
log "  Consider SystemMaxUse= in /etc/systemd/journald.conf for a persistent bound."

log "done"
