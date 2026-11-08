#!/bin/sh
# ops/backup.sh — consistent SQLite snapshot(s) + config bundle, with optional
# offsite restic push and bounded local retention.
#
# Run by the sq-backup.service systemd unit (daily, see ops/systemd).
# Never echoes secrets: restic reads RESTIC_PASSWORD/RESTIC_PASSWORD_FILE and
# RESTIC_REPOSITORY from the environment (e.g. an EnvironmentFile) on its own;
# this script never prints them.
#
# Configuration (all via environment, all optional):
#   SQ_HOME                repo root override (default: two levels above this script)
#   SQ_BACKUP_ROOT          local backup root (default: $SQ_HOME/user_data/runtime/backups)
#   SQ_BACKUP_KEEP_LOCAL    local backups to retain (default: 7); only timestamp-named
#                           directories under a root holding the .sq-backup-root
#                           marker (created by this script) are ever pruned
#   RESTIC_REPOSITORY       if set, also push the bundle to this restic repository
#                           (tagged sq-backup) and prune only sq-backup-tagged
#                           snapshots with --keep-daily 14 --keep-weekly 8
#
# Snapshots every user_data/runtime/*.sqlite file that exists, whatever its
# name (e.g. dry-run.sqlite in dry-run mode, live.sqlite per config/live.json
# in live mode) — never assumes a single fixed name, and never creates one:
# if none exist, this is a hard failure, not a silently empty backup. Also
# copies user_data/runtime/jev/*.jsonl (Jev audit records), if any exist,
# into the backup's jev/ subdirectory.
#
# Exit non-zero on any failure, in particular a failed integrity check: a
# database snapshot that fails PRAGMA integrity_check is never promoted to
# the local backup root or pushed to restic (and neither is anything else
# from that run).
set -eu
umask 077

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=${SQ_HOME:-$(CDPATH='' cd -- "$script_dir/.." && pwd)}
backup_root=${SQ_BACKUP_ROOT:-"$repo_root/user_data/runtime/backups"}
keep_local=${SQ_BACKUP_KEEP_LOCAL:-7}

# Local retention only ever touches directories named like the ones this script
# creates (see $timestamp below), and only under a root carrying this marker.
ts_glob='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z'
marker_name=.sq-backup-root

runtime_dir="$repo_root/user_data/runtime"
jev_dir="$runtime_dir/jev"
staging_dir="$runtime_dir/.backup-staging"
container_runtime_dir="/freqtrade/user_data/runtime"
container_staging_dir="$container_runtime_dir/.backup-staging"

dc() {
    # cd, not --project-directory: Compose resolves compose.yaml, .env and
    # COMPOSE_FILE (e.g. the VPS overlay, see compose.vps.example.yaml)
    # relative to the current working directory, not --project-directory.
    (cd "$repo_root" && docker compose "$@")
}

container_running() {
    dc ps --status running --services 2>/dev/null | grep -qx freqtrade
}

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

staged_secrets_dir=
# Set while this run's own timestamp directory is incomplete; removed on failure
# so retention never counts a partial backup as one of the $keep_local kept.
partial_dest=
cleanup_staged_secrets() {
    # config/local is copied into the local bundle only long enough for restic
    # to encrypt it.  The traps below are essential here: `set -e`, a failed
    # restic command, or an interrupt must not leave that plaintext copy under
    # the retained backup root.  dash does not run an EXIT trap when killed by
    # a signal (systemd stop/timeout sends SIGTERM), hence the signal traps.
    if [ -n "$staged_secrets_dir" ] && [ -d "$staged_secrets_dir" ]; then
        rm -rf "$staged_secrets_dir"
        log "removed staged config/local from the local backup"
    fi
    if [ -n "$partial_dest" ] && [ -d "$partial_dest" ]; then
        rm -rf "${partial_dest:?}"
        log "removed incomplete backup $partial_dest"
    fi
}
trap cleanup_staged_secrets 0
# 128 + signal number, after cleanup: a stopped backup must not look successful.
trap 'cleanup_staged_secrets; exit 130' INT
trap 'cleanup_staged_secrets; exit 143' TERM
trap 'cleanup_staged_secrets; exit 129' HUP

fail() {
    log "ERROR: $1"
    exit 1
}

# snapshot_db $1: back up one user_data/runtime/<name>.sqlite into
# $staging_dir/<name>, integrity-checked in place. Fails (and leaves the
# staged file for inspection) rather than promoting a bad snapshot.
snapshot_db() {
    name="$1"
    staging_db="$staging_dir/$name"
    if [ "$running" = true ]; then
        container_db="$container_runtime_dir/$name"
        container_staging_db="$container_staging_dir/$name"
        # </dev/null: `docker compose exec` forwards stdin, which would
        # otherwise swallow the caller's remaining input.
        dc exec -T freqtrade sqlite3 "$container_db" ".backup '$container_staging_db'" </dev/null \
            || fail "sqlite3 .backup failed inside the container for $name"
        integrity=$(dc exec -T freqtrade sqlite3 "$container_staging_db" "PRAGMA integrity_check;" </dev/null) \
            || fail "integrity_check failed to run inside the container for $name"
    else
        host_db="$runtime_dir/$name"
        sqlite3 "$host_db" ".backup '$staging_db'" || fail "sqlite3 .backup failed on the host for $name"
        integrity=$(sqlite3 "$staging_db" "PRAGMA integrity_check;") || fail "integrity_check failed to run for $name"
    fi
    integrity=$(printf '%s' "$integrity" | tr -d '\r\n ')
    [ "$integrity" = "ok" ] \
        || fail "PRAGMA integrity_check on $name reported: $integrity (snapshot kept at $staging_dir for inspection)"
    log "integrity_check ok for $name"
}

# Checked before anything is written: 0 or a negative count would make local
# retention prune every backup, including the one this run is about to write.
case $keep_local in
    '' | *[!0-9]*) fail "SQ_BACKUP_KEEP_LOCAL must be a positive integer, got '$keep_local'" ;;
esac
[ "$keep_local" -ge 1 ] || fail "SQ_BACKUP_KEEP_LOCAL must be a positive integer, got '$keep_local'"

rm -rf "$staging_dir"
mkdir -p "$staging_dir"

# Discover every database to snapshot. -name '*.sqlite' excludes the
# -wal/-shm siblings and any stray *.pre-restore.* file (see ops/restore.sh)
# since neither ends in a literal ".sqlite".
db_names=$(find "$runtime_dir" -maxdepth 1 -type f -name '*.sqlite' -exec basename {} \; 2>/dev/null | LC_ALL=C sort)
[ -n "$db_names" ] || fail "no *.sqlite database found under $runtime_dir; nothing to back up"

if container_running; then
    running=true
    log "container running: snapshotting via docker compose exec"
else
    running=false
    log "container not running: snapshotting the host copy"
    command -v sqlite3 >/dev/null 2>&1 || fail "container is down and no host sqlite3 binary is available"
fi

# A for loop, not `printf | while read`: no pipe feeding stdin to the
# snapshot commands, and a failure exits this shell rather than a subshell.
# Names come from find in one directory and are split on newlines only.
old_ifs=$IFS
IFS='
'
for name in $db_names; do
    snapshot_db "$name"
done
# The container writes the staged snapshots; never promote (or chmod through)
# a symlink planted in their place. Checked before $dest exists, so a refused
# run leaves no partial timestamp directory for retention to count.
for name in $db_names; do
    if [ -L "$staging_dir/$name" ] || [ ! -f "$staging_dir/$name" ]; then
        fail "staged snapshot $staging_dir/$name is not a regular file"
    fi
done

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$backup_root/$timestamp"
mkdir -p "$backup_root"
# No -p: this run must create $dest itself before it may remove it on failure.
mkdir "$dest"
partial_dest=$dest
mkdir "$dest/config"

# Claim the backup root for local retention: only when it is new or holds
# nothing but this script's own timestamp directories. A shared directory with
# unrelated entries and no marker is never pruned.
marker="$backup_root/$marker_name"
if [ ! -e "$marker" ]; then
    foreign=$(find "$backup_root" -mindepth 1 -maxdepth 1 ! -name "$ts_glob" 2>/dev/null)
    if [ -z "$foreign" ]; then
        : >"$marker"
        log "marked $backup_root as a backup root ($marker_name)"
    fi
fi

for name in $db_names; do
    mv "$staging_dir/$name" "$dest/$name"
    # The in-container sqlite3 runs under the image umask (022), not this
    # script's 077: tighten the snapshot explicitly.
    chmod 600 "$dest/$name"
done
IFS=$old_ifs
rm -rf "$staging_dir"

# Bundle the tracked config. config/local holds secrets and is staged only
# immediately before an enabled restic push below: an unencrypted local-only
# backup directory must not duplicate it outside config/local's permissions.
for entry in "$repo_root"/config/*; do
    name=$(basename "$entry")
    [ "$name" = "local" ] && continue
    cp -R "$entry" "$dest/config/"
done
if [ -z "${RESTIC_REPOSITORY:-}" ]; then
    log "excluded config/local (no RESTIC_REPOSITORY configured)"
fi

# Jev audit records (never secrets, never exchange credentials) are optional:
# only copy them if the directory holds any.
if [ -d "$jev_dir" ]; then
    jev_files=$(find "$jev_dir" -maxdepth 1 -type f -name '*.jsonl' 2>/dev/null)
    if [ -n "$jev_files" ]; then
        mkdir -p "$dest/jev"
        printf '%s\n' "$jev_files" | while IFS= read -r f; do
            [ -n "$f" ] || continue
            cp "$f" "$dest/jev/"
        done
        log "copied jev audit records to $dest/jev"
    fi
fi

# Atomically published forward evidence, window contract and STOP latch.
if [ -d "$runtime_dir/reports" ]; then
    mkdir -p "$dest/reports"
    for report in "$runtime_dir"/reports/*.json; do
        # The reports directory is writable from the containers: skip symlinks
        # so a planted link cannot pull a host file into the backup.
        [ -f "$report" ] && [ ! -L "$report" ] || continue
        cp -P "$report" "$dest/reports/"
        chmod 600 "$dest/reports/$(basename "$report")"
    done
fi

partial_dest=
log "backup written to $dest"

# Defense in depth: strip config/local from every locally retained backup,
# including any left behind by a prior run of this script before this
# cleanup existed. Local retention must never hold secrets in cleartext.
# Only this script's own timestamp directories are looked at, and only under a
# root carrying the marker (the same ownership rule as pruning below).
if [ -e "$marker" ]; then
    find "$backup_root" -mindepth 3 -maxdepth 3 -type d -path "*/$ts_glob/config/local" -print \
        | while IFS= read -r stray; do
            rm -rf "${stray:?}"
            log "removed stray secrets directory $stray from local retention"
        done
fi

# Bound local retention regardless of restic: keep the newest $keep_local
# timestamped directories under $backup_root. Only directories named like the
# ones this script creates are candidates, and only if the root carries the
# marker this script created; everything else in the root is left alone.
if [ ! -e "$marker" ]; then
    log "not pruning $backup_root: no $marker_name marker and it holds unrelated entries"
else
    count=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name "$ts_glob" | wc -l | tr -d ' ')
    if [ "$count" -gt "$keep_local" ]; then
        to_remove=$((count - keep_local))
        find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name "$ts_glob" -print \
            | LC_ALL=C sort \
            | head -n "$to_remove" \
            | while IFS= read -r old; do
                log "pruning local backup $old"
                rm -rf "${old:?}"
            done
    fi
fi

# Offsite push last: a failing restic run must not skip the local pruning above.
if [ -n "${RESTIC_REPOSITORY:-}" ]; then
    if ! command -v restic >/dev/null 2>&1; then
        fail "RESTIC_REPOSITORY is set but the restic binary is not installed"
    fi
    if [ -d "$repo_root/config/local" ]; then
        staged_secrets_dir="$dest/config/local"
        cp -R "$repo_root/config/local" "$staged_secrets_dir"
        log "included config/local (restic will encrypt this bundle)"
    fi
    log "pushing to restic repository"
    restic backup --tag sq-backup "$dest" >/dev/null
    # Remove the plaintext staging copy immediately after a successful push;
    # the EXIT trap performs the same cleanup on every failure path.
    cleanup_staged_secrets
    log "pruning restic snapshots (--keep-daily 14 --keep-weekly 8)"
    restic forget --tag sq-backup --group-by host,tags --keep-daily 14 --keep-weekly 8 --prune >/dev/null
fi

log "done"
