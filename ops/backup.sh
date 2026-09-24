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
#   SQ_BACKUP_KEEP_LOCAL    local backups to retain (default: 7)
#   RESTIC_REPOSITORY       if set, also push the bundle to this restic repository
#                           and prune with --keep-daily 14 --keep-weekly 8
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

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
dest="$backup_root/$timestamp"
mkdir -p "$dest" "$dest/config"

for name in $db_names; do
    mv "$staging_dir/$name" "$dest/$name"
    # The in-container sqlite3 runs under the image umask (022), not this
    # script's 077: tighten the snapshot explicitly.
    chmod 600 "$dest/$name"
done
IFS=$old_ifs
rm -rf "$staging_dir"

# Bundle the tracked config. config/local holds secrets, so it is only
# included when RESTIC_REPOSITORY is set: restic encrypts the bundle before
# it leaves the host, but an unencrypted local-only backup directory should
# not duplicate secrets outside config/local's own permissions.
for entry in "$repo_root"/config/*; do
    name=$(basename "$entry")
    [ "$name" = "local" ] && continue
    cp -R "$entry" "$dest/config/"
done
if [ -n "${RESTIC_REPOSITORY:-}" ] && [ -d "$repo_root/config/local" ]; then
    cp -R "$repo_root/config/local" "$dest/config/local"
    log "included config/local (restic will encrypt this bundle)"
else
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

log "backup written to $dest"

if [ -n "${RESTIC_REPOSITORY:-}" ]; then
    if ! command -v restic >/dev/null 2>&1; then
        fail "RESTIC_REPOSITORY is set but the restic binary is not installed"
    fi
    log "pushing to restic repository"
    restic backup "$dest" >/dev/null
    # config/local (secrets) was only staged above so restic could encrypt it
    # off-host. Once the push has succeeded, remove it from the local,
    # unencrypted backup dir immediately: it must never persist in cleartext
    # under $backup_root or be kept by local retention below.
    if [ -d "$dest/config/local" ]; then
        rm -rf "$dest/config/local"
        log "removed staged config/local from the local backup after the restic push"
    fi
    log "pruning restic snapshots (--keep-daily 14 --keep-weekly 8)"
    restic forget --keep-daily 14 --keep-weekly 8 --prune >/dev/null
fi

# Defense in depth: strip config/local from every locally retained backup,
# including any left behind by a prior run of this script before this
# cleanup existed. Local retention must never hold secrets in cleartext.
find "$backup_root" -mindepth 3 -maxdepth 3 -type d -path '*/config/local' -print \
    | while IFS= read -r stray; do
        rm -rf "$stray"
        log "removed stray secrets directory $stray from local retention"
    done

# Bound local retention regardless of restic: keep the newest $keep_local
# timestamped directories under $backup_root.
count=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
if [ "$count" -gt "$keep_local" ]; then
    to_remove=$((count - keep_local))
    find "$backup_root" -mindepth 1 -maxdepth 1 -type d -print \
        | sort \
        | head -n "$to_remove" \
        | while IFS= read -r old; do
            log "pruning local backup $old"
            rm -rf "${old:?}"
        done
fi

log "done"
