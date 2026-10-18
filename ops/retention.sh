#!/bin/sh
# ops/retention.sh — bound disk usage on the VPS: prune dangling Docker
# images, remove superseded freqtradeorg/freqtrade images and print journald
# retention guidance. Safe by default: prints what
# it would do and changes nothing unless --apply is given.
#
# Usage: ops/retention.sh [--apply]
#
# Superseded images: with --apply, every local image of the freqtradeorg/freqtrade
# repository (and only that repository) whose digest differs from the
# digest pinned in compose.yaml is removed by reference, without --force, so an
# image still used by a container is kept. The pinned digest is never removed;
# if it cannot be read from compose.yaml (or is ambiguous), no such image is
# removed. Images without a known digest are skipped. SQ_HOME overrides the
# repo root.
#
# OHLCV cache pruning under user_data/data was deliberately removed: the bot
# container runs Freqtrade in trade mode only and does not mount or write to
# user_data/data, so there is nothing there for this script to prune.
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=${SQ_HOME:-$(CDPATH='' cd -- "$script_dir/.." && pwd)}
image_repo=freqtradeorg/freqtrade

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

# Superseded freqtradeorg/freqtrade images. The jev/tools/research services
# share the one image pinned in compose.yaml.
pinned=$(grep -Eo "$image_repo:[^ @]+@sha256:[0-9a-f]{64}" "$repo_root/compose.yaml" 2>/dev/null \
    | sed 's/.*@//' | sort -u) || pinned=""
if [ -z "$pinned" ] || [ "$(printf '%s\n' "$pinned" | wc -l | tr -d ' ')" -ne 1 ]; then
    log "cannot read a single pinned $image_repo digest from $repo_root/compose.yaml; not removing any $image_repo images"
else
    log "pinned $image_repo digest: $pinned"
    # One "repository tag digest" row per local reference; the repository is
    # matched exactly and only well-formed digests other than the pinned one
    # are candidates.
    old_refs=$(docker image ls --digests --no-trunc --format '{{.Repository}} {{.Tag}} {{.Digest}}' "$image_repo" \
        | awk -v repo="$image_repo" -v pin="$pinned" \
            '$1 == repo && $3 ~ /^sha256:[0-9a-f]+$/ && $3 != pin { if ($2 == "<none>") print repo "@" $3; else print repo ":" $2 }' \
        | sort -u) || old_refs=""
    if [ -z "$old_refs" ]; then
        log "no superseded $image_repo images"
    else
        for ref in $old_refs; do
            if [ "$apply" = true ]; then
                log "removing superseded image $ref"
                docker image rm "$ref" || log "could not remove $ref (in use?); left in place"
            else
                log "[dry-run] would run: docker image rm $ref"
            fi
        done
    fi
fi

log "journald guidance (not applied by this script):"
log "  journalctl --disk-usage                 # check current journal size"
log "  sudo journalctl --vacuum-time=30d        # or --vacuum-size=500M"
log "  Consider SystemMaxUse= in /etc/systemd/journald.conf for a persistent bound."

log "done"
