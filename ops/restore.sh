#!/bin/sh
# ops/restore.sh — restore a backup.sh snapshot into the trade database path,
# leaving the bot stopped so an operator must explicitly resume trading.
#
# Usage:
#   ops/restore.sh --local <backup-dir-or-sqlite-file> [--db <name>] [--with-evidence]
#   ops/restore.sh --restic <snapshot-id> [--db <name>] [--with-evidence]
#
# backup.sh snapshots every user_data/runtime/*.sqlite file that exists (e.g.
# dry-run.sqlite, live.sqlite), so a backup directory or restic snapshot may
# hold more than one database. Which one to restore:
#   - --db <name> (e.g. "live.sqlite"), if given: restore exactly that one.
#   - otherwise, if the source holds exactly one *.sqlite, restore that one.
#   - otherwise, fail and list the names found: never guess.
# For a single sqlite *file* passed to --local, the restored name is --db if
# given, else that file's own basename. The restored database always replaces
# user_data/runtime/<that name>, i.e. --db (or the resolved name) selects
# *which* live database this run replaces, not just what to read.
#
# The current live database is never deleted: it is renamed aside with a
# ".pre-restore.<timestamp>" suffix next to itself, and so are its -wal/-shm
# siblings if present (a stale WAL could otherwise be replayed onto the
# restored database on next open). The restored snapshot is integrity-checked
# twice: once in staging before it touches the live path, and once more after
# it replaces the live database.
#
# --with-evidence also restores the forward-test evidence from the same backup:
# reports/*.json (window manifest, STOP latch, latest report) and
# market-data.sqlite, taken from the directory holding the chosen database. Each
# file it replaces is first renamed aside to <name>.pre-restore.<timestamp>;
# nothing is deleted, and files that exist only on the host are left alone.
#
# If the integrity check of the restored database fails, the container is left
# stopped and the error names the .pre-restore.<timestamp> file to move back.
#
# The container is stopped for the swap and started again at the end with
# `docker compose up -d`. Because config/base.json sets initial_state=stopped,
# Freqtrade reads its bot state from configuration at startup rather than
# from the database, so the bot always comes back in the `stopped` state
# regardless of what it was doing before the restore. Resuming trading is a
# separate, explicit operator action (Telegram /start or the API).
set -eu
umask 077

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=${SQ_HOME:-$(CDPATH='' cd -- "$script_dir/.." && pwd)}

runtime_dir="$repo_root/user_data/runtime"
staging_dir="$runtime_dir/.restore-staging"
container_runtime_dir="/freqtrade/user_data/runtime"
container_staging_dir="$container_runtime_dir/.restore-staging"
restic_target="$runtime_dir/.restore-staging-restic"

lock_dir="$runtime_dir/.restore.lock"

cleanup_staging() {
    # Both staging dirs can hold plaintext config/secrets from a restic
    # restore; remove them on every exit path, signals included (dash does not
    # run an EXIT trap when a signal kills the script). Installed only once
    # this run holds $lock_dir, so a second invocation never removes the
    # staging of a restore that is still running.
    rm -rf "${staging_dir:?}" "${restic_target:?}"
    rmdir "$lock_dir" 2>/dev/null || :
}

dc() {
    # cd, not --project-directory: Compose resolves compose.yaml, .env and
    # COMPOSE_FILE (e.g. the VPS overlay, see compose.vps.example.yaml)
    # relative to the current working directory, not --project-directory.
    (cd "$repo_root" && docker compose "$@")
}

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"
}

fail() {
    log "ERROR: $1"
    exit 1
}

usage() {
    printf 'Usage: %s --local <backup-dir-or-sqlite-file> [--db <name>] [--with-evidence] | --restic <snapshot-id> [--db <name>] [--with-evidence]\n' "$0" >&2
}

# Recovery pointer appended to integrity failures once the live files have been
# replaced; empty before that.
integrity_hint=""

check_integrity() {
    # $1: container-visible path to the sqlite file to check.
    result=$(dc run --rm --no-deps --entrypoint sqlite3 freqtrade "$1" "PRAGMA integrity_check;") \
        || fail "integrity_check failed to run against $1${integrity_hint:+; $integrity_hint}"
    result=$(printf '%s' "$result" | tr -d '\r\n ')
    [ "$result" = "ok" ] || fail "PRAGMA integrity_check on $1 reported: $result${integrity_hint:+; $integrity_hint}"
}

# move_aside $1: rename an existing file to <file>.pre-restore.$ts (never delete).
move_aside() {
    if [ -f "$1" ]; then
        mv "$1" "$1.pre-restore.$ts"
        log "moved $1 aside to $1.pre-restore.$ts"
    fi
}

# stage_evidence: copy reports/*.json and market-data.sqlite from the directory
# holding the chosen database into $staging_dir/evidence. Sets have_market_data
# and have_reports.
stage_evidence() {
    src=$(dirname "$resolved_path")
    mkdir -p "$staging_dir/evidence"
    have_market_data=false
    have_reports=false
    if [ "$target_name" != market-data.sqlite ] && [ -f "$src/market-data.sqlite" ]; then
        cp "$src/market-data.sqlite" "$staging_dir/evidence/market-data.sqlite"
        have_market_data=true
    fi
    if [ -d "$src/reports" ]; then
        mkdir -p "$staging_dir/evidence/reports"
        for report in "$src"/reports/*.json; do
            [ -f "$report" ] || continue
            cp "$report" "$staging_dir/evidence/reports/"
            have_reports=true
        done
    fi
    if [ "$have_market_data" = false ] && [ "$have_reports" = false ]; then
        fail "--with-evidence: no reports/*.json or market-data.sqlite next to $resolved_path"
    fi
}

# resolve_from_dir $1: pick which *.sqlite under directory $1 to restore,
# honoring --db if set. Sets $resolved_path (the file found) and $target_name
# (the user_data/runtime/<name> this restore will replace). Searches
# recursively: a restic restore preserves the snapshot's original absolute
# path prefix under $1, a plain local backup directory does not, and this
# works for both without needing to know which.
resolve_from_dir() {
    dir="$1"
    if [ -n "$db_name" ]; then
        resolved_path=$(find "$dir" -type f -name "$db_name" -print -quit 2>/dev/null)
        [ -n "$resolved_path" ] || fail "no $db_name found under $dir"
        target_name="$db_name"
        return
    fi
    matches=$(find "$dir" -type f -name '*.sqlite' 2>/dev/null | LC_ALL=C sort)
    [ -n "$matches" ] || fail "no *.sqlite database found under $dir"
    count=$(printf '%s\n' "$matches" | wc -l | tr -d ' ')
    if [ "$count" -eq 1 ]; then
        resolved_path="$matches"
        target_name=$(basename "$resolved_path")
    else
        names=$(printf '%s\n' "$matches" | while IFS= read -r m; do basename "$m"; done | LC_ALL=C sort -u | tr '\n' ' ')
        fail "multiple databases found under $dir ($names); pass --db <name> to choose one"
    fi
}

mode=""
source_arg=""
db_name=""
with_evidence=false
while [ $# -gt 0 ]; do
    case "$1" in
        --local | --restic)
            [ -z "$mode" ] || {
                usage
                exit 1
            }
            [ $# -ge 2 ] || {
                usage
                exit 1
            }
            mode="$1"
            source_arg="$2"
            shift 2
            ;;
        --with-evidence)
            with_evidence=true
            shift
            ;;
        --db)
            [ $# -ge 2 ] || {
                usage
                exit 1
            }
            db_name="$2"
            shift 2
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done
[ -n "$mode" ] || {
    usage
    exit 1
}
# The name becomes a path under user_data/runtime (and a find -name pattern):
# a bare *.sqlite file name only, without path separators or glob characters.
check_db_name() {
    case "$2" in
        [!./]*.sqlite) ;;
        *) fail "$1 must be a database file name such as live.sqlite, got: $2" ;;
    esac
    case "$2" in */* | *[*?[]*) fail "$1 must be a plain file name: $2" ;; esac
}
[ -z "$db_name" ] || check_db_name --db "$db_name"

# One restore at a time (mkdir is atomic; flock is not on macOS for the local
# rehearsal). A lock left by a killed run (SIGKILL skips the traps) must be
# removed by hand once no restore is running.
mkdir -p "$runtime_dir"
mkdir "$lock_dir" 2>/dev/null \
    || fail "another restore is running, or a killed one left $lock_dir: remove it once none is"
trap cleanup_staging 0
trap 'trap - 0; cleanup_staging; exit 130' INT
trap 'trap - 0; cleanup_staging; exit 143' TERM
trap 'trap - 0; cleanup_staging; exit 129' HUP

rm -rf "$staging_dir"
mkdir -p "$staging_dir"

case "$mode" in
    --local)
        if [ -d "$source_arg" ]; then
            resolve_from_dir "$source_arg"
        elif [ -f "$source_arg" ]; then
            resolved_path="$source_arg"
            target_name=${db_name:-$(basename "$source_arg")}
            check_db_name "the restored file name (pass --db)" "$target_name"
        else
            fail "$source_arg is neither a backup directory nor a file"
        fi
        cp "$resolved_path" "$staging_dir/$target_name"
        [ "$with_evidence" = false ] || stage_evidence
        ;;
    --restic)
        command -v restic >/dev/null 2>&1 || fail "restic is not installed on this host"
        [ -n "${RESTIC_REPOSITORY:-}" ] || fail "RESTIC_REPOSITORY is not set"
        rm -rf "$restic_target"
        mkdir -p "$restic_target"
        restic restore "$source_arg" --target "$restic_target" \
            || fail "restic restore of snapshot $source_arg failed"
        resolve_from_dir "$restic_target"
        cp "$resolved_path" "$staging_dir/$target_name"
        [ "$with_evidence" = false ] || stage_evidence
        rm -rf "$restic_target"
        ;;
    *)
        usage
        exit 1
        ;;
esac

staging_db="$staging_dir/$target_name"
container_staging_db="$container_staging_dir/$target_name"
host_db="$runtime_dir/$target_name"
container_db="/freqtrade/user_data/runtime/$target_name"

log "restoring $target_name"
log "checking integrity of the staged snapshot before touching the live database"
check_integrity "$container_staging_db"
log "staged snapshot integrity_check ok"
if [ "$with_evidence" = true ] && [ "$have_market_data" = true ]; then
    check_integrity "$container_staging_dir/evidence/market-data.sqlite"
    log "staged market-data.sqlite integrity_check ok"
fi

log "stopping the freqtrade service"
dc stop freqtrade

ts=$(date -u +%Y%m%dT%H%M%SZ)
aside=""
if [ -f "$host_db" ]; then
    aside="$host_db.pre-restore.$ts"
    mv "$host_db" "$aside"
    log "moved the current database aside to $aside"
else
    log "no existing database at $host_db; nothing to move aside"
fi
# A stale -wal/-shm could otherwise be replayed onto the restored database
# the next time it is opened, silently undoing the restore. Move them aside
# with the same suffix regardless of whether the main file existed above.
for suffix in -wal -shm; do
    sib="$host_db$suffix"
    if [ -f "$sib" ]; then
        mv "$sib" "$sib.pre-restore.$ts"
        log "moved $sib aside to $sib.pre-restore.$ts"
    fi
done

cp "$staging_db" "$host_db"
log "restored snapshot copied to $host_db"

if [ "$with_evidence" = true ]; then
    if [ "$have_market_data" = true ]; then
        move_aside "$runtime_dir/market-data.sqlite"
        move_aside "$runtime_dir/market-data.sqlite-wal"
        move_aside "$runtime_dir/market-data.sqlite-shm"
        cp "$staging_dir/evidence/market-data.sqlite" "$runtime_dir/market-data.sqlite"
        log "restored market-data.sqlite"
    fi
    if [ "$have_reports" = true ]; then
        mkdir -p "$runtime_dir/reports"
        for report in "$staging_dir"/evidence/reports/*.json; do
            dest="$runtime_dir/reports/$(basename "$report")"
            move_aside "$dest"
            cp "$report" "$dest"
        done
        log "restored reports/*.json (reports only on this host were left in place)"
    fi
fi

if [ -n "$aside" ]; then
    integrity_hint="the freqtrade service is left stopped; the previous database is at $aside (move it back to $host_db to undo); other replaced files were kept as <name>.pre-restore.$ts"
else
    integrity_hint="the freqtrade service is left stopped; no previous database existed at $host_db; other replaced files were kept as <name>.pre-restore.$ts"
fi
log "checking integrity of the restored live database"
check_integrity "$container_db"
if [ "$with_evidence" = true ] && [ "$have_market_data" = true ]; then
    check_integrity "$container_runtime_dir/market-data.sqlite"
fi
integrity_hint=""
log "restored database integrity_check ok"

log "starting the freqtrade service (bot state will be 'stopped' per initial_state)"
dc up -d freqtrade

log "done. The bot is running but NOT trading (initial_state: stopped)."
log "Resume trading explicitly via Telegram /start or POST /api/v1/start once you have verified the restore."
