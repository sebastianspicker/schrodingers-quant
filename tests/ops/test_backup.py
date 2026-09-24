"""Integration tests for ops/backup.sh: runs the real script as a subprocess
against a temporary SQ_HOME, on both of its paths. The pinned test image has no
`docker` binary, so the host-sqlite3 path runs as is; the container path runs
against a stub `docker` (see `_docker_stub`) that, like the real CLI, reads
stdin and maps container paths onto SQ_HOME.

ops/restore.sh is covered by tests/ops/test_restore.py.
"""

import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKUP_SH = REPO_ROOT / "ops" / "backup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None,
    reason="ops/backup.sh's host-sqlite3 path requires the sqlite3 CLI on PATH",
)


def _make_sqlite_db(path: Path) -> None:
    """A minimal but real sqlite database: one table, one row."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, pair TEXT)")
        conn.execute("INSERT INTO trades (pair) VALUES ('ETH/USD')")
        conn.commit()
    finally:
        conn.close()


def _sq_home(tmp_path: Path) -> Path:
    """A throwaway repo root: just enough of config/ and user_data/runtime/
    for ops/backup.sh to have something to bundle and snapshot."""
    sq_home = tmp_path / "sq_home"
    (sq_home / "config" / "local").mkdir(parents=True)
    (sq_home / "user_data" / "runtime").mkdir(parents=True)
    (sq_home / "config" / "base.json").write_text('{"dry_run": true}\n')
    (sq_home / "config" / "local" / "secrets.json").write_text('{"secret": "do-not-copy"}\n')
    return sq_home


# Stands in for `docker compose ps|exec -T freqtrade sqlite3 ...`. It drains
# stdin first, as `docker compose exec` forwards it: a caller that feeds the
# snapshot loop through a pipe loses its remaining input (a real regression).
DOCKER_STUB = """#!/usr/bin/env python3
import os, subprocess, sys
sys.stdin.read()
args = sys.argv[2:]  # drop "compose"
if args[0] == "ps":
    print("freqtrade")
    sys.exit(0)
assert args[:3] == ["exec", "-T", "freqtrade"], args
runtime = os.environ["SQ_HOME"] + "/user_data/runtime"
mapped = [a.replace("/freqtrade/user_data/runtime", runtime) for a in args[3:]]
sys.exit(subprocess.run(mapped).returncode)
"""


def _docker_stub(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(DOCKER_STUB)
    stub.chmod(0o755)
    return bin_dir


def _run_backup(sq_home: Path, extra_path: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
    # Isolate from whatever the invoking shell happens to have set: this
    # test only ever wants the local, no-restic, host-sqlite3 path against
    # the temporary SQ_HOME below.
    for var in ("RESTIC_REPOSITORY", "SQ_BACKUP_ROOT", "SQ_BACKUP_KEEP_LOCAL", "COMPOSE_FILE"):
        env.pop(var, None)
    env["SQ_HOME"] = str(sq_home)
    return subprocess.run(
        [str(BACKUP_SH)], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


def _backup_dirs(sq_home: Path) -> list[Path]:
    root = sq_home / "user_data" / "runtime" / "backups"
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


@pytest.mark.parametrize("container_running", [False, True], ids=["host", "container"])
def test_backup_snapshots_every_sqlite_file_present(tmp_path, container_running):
    sq_home = _sq_home(tmp_path)
    runtime = sq_home / "user_data" / "runtime"
    _make_sqlite_db(runtime / "dry-run.sqlite")
    _make_sqlite_db(runtime / "live.sqlite")
    (runtime / "jev").mkdir()
    (runtime / "jev" / "candidates.jsonl").write_text('{"id": 1}\n')

    result = _run_backup(sq_home, _docker_stub(tmp_path) if container_running else None)
    assert result.returncode == 0, result.stdout + result.stderr
    expected_path = "container running" if container_running else "container not running"
    assert expected_path in result.stdout

    dirs = _backup_dirs(sq_home)
    assert len(dirs) == 1
    dest = dirs[0]

    for name in ("dry-run.sqlite", "live.sqlite"):
        db_path = dest / name
        assert db_path.is_file(), f"{name} missing from {dest}"
        mode = stat.S_IMODE(db_path.stat().st_mode)
        assert mode == 0o600, f"{name} has mode {oct(mode)}, expected 0o600"
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT pair FROM trades").fetchall()
        finally:
            conn.close()
        assert rows == [("ETH/USD",)]

    jev_file = dest / "jev" / "candidates.jsonl"
    assert jev_file.is_file()
    assert jev_file.read_text() == '{"id": 1}\n'

    assert (dest / "config" / "base.json").is_file()
    assert not (dest / "config" / "local").exists()

    # No stray sqlite file anywhere under the backup dir besides the two
    # promoted snapshots (e.g. the old "sqlite3 .backup" on a missing source
    # silently exits 0 and creates an empty file" defect this replaces).
    stray = [p for p in dest.rglob("*.sqlite") if p.parent != dest]
    assert stray == []

    # And none left behind (or newly created) under user_data/runtime/
    # itself: staging is cleaned up, and the host DBs are untouched originals.
    assert not (runtime / ".backup-staging").exists()
    runtime_sqlite_files = sorted(p.name for p in runtime.glob("*.sqlite"))
    assert runtime_sqlite_files == ["dry-run.sqlite", "live.sqlite"]


def test_backup_fails_with_no_sqlite_files(tmp_path):
    sq_home = _sq_home(tmp_path)

    result = _run_backup(sq_home)

    assert result.returncode != 0, result.stdout + result.stderr
    assert not _backup_dirs(sq_home)
