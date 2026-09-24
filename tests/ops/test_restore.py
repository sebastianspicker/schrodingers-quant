"""Integration tests for ops/restore.sh against a temporary SQ_HOME.

The pinned test image has no `docker` binary, so a stub stands in for the
three Compose calls restore.sh makes: `run ... --entrypoint sqlite3 freqtrade`
(integrity checks, with container paths mapped onto SQ_HOME), `stop` and
`up -d` (recorded, otherwise no-ops).
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESTORE_SH = REPO_ROOT / "ops" / "restore.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None, reason="the docker stub runs the sqlite3 CLI"
)

DOCKER_STUB = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[2:]  # drop "compose"
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(" ".join(args[:2]) + "\\n")
if args[0] in ("stop", "up"):
    sys.exit(0)
assert args[:6] == ["run", "--rm", "--no-deps", "--entrypoint", "sqlite3", "freqtrade"], args
runtime = os.environ["SQ_HOME"] + "/user_data/runtime"
mapped = [a.replace("/freqtrade/user_data/runtime", runtime) for a in args[6:]]
sys.exit(subprocess.run(["sqlite3", *mapped]).returncode)
"""


def _make_db(path: Path, marker: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE drill (x TEXT)")
        conn.execute("INSERT INTO drill VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _markers(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT x FROM drill")]
    finally:
        conn.close()


@pytest.fixture
def sq_home(tmp_path):
    home = tmp_path / "sq_home"
    runtime = home / "user_data" / "runtime"
    runtime.mkdir(parents=True)
    backup = runtime / "backups" / "20260101T000000Z"
    backup.mkdir(parents=True)
    _make_db(backup / "dry-run.sqlite", "dry-run backup")
    _make_db(backup / "live.sqlite", "live backup")
    _make_db(runtime / "live.sqlite", "live current")
    (runtime / "live.sqlite-wal").write_bytes(b"stale wal")
    return home


def _restore(sq_home: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(DOCKER_STUB)
    stub.chmod(0o755)
    env = dict(os.environ)
    env.pop("RESTIC_REPOSITORY", None)
    env.update(
        SQ_HOME=str(sq_home),
        STUB_LOG=str(tmp_path / "docker.log"),
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
    )
    return subprocess.run(
        ["sh", str(RESTORE_SH), *args],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _backup_dir(sq_home: Path) -> str:
    return str(sq_home / "user_data" / "runtime" / "backups" / "20260101T000000Z")


def test_restore_refuses_to_guess_between_several_databases(sq_home, tmp_path):
    result = _restore(sq_home, tmp_path, "--local", _backup_dir(sq_home))

    assert result.returncode != 0
    assert "multiple databases" in result.stdout
    assert "dry-run.sqlite" in result.stdout and "live.sqlite" in result.stdout
    runtime = sq_home / "user_data" / "runtime"
    assert _markers(runtime / "live.sqlite") == ["live current"]
    assert not (tmp_path / "docker.log").exists(), "nothing may be stopped before resolving"


def test_restore_named_database_moves_current_file_and_wal_aside(sq_home, tmp_path):
    runtime = sq_home / "user_data" / "runtime"

    result = _restore(sq_home, tmp_path, "--local", _backup_dir(sq_home), "--db", "live.sqlite")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (runtime / "live.sqlite-wal").exists()
    assert _markers(runtime / "live.sqlite") == ["live backup"]
    aside = sorted(p.name for p in runtime.glob("live.sqlite*.pre-restore.*"))
    assert len(aside) == 2
    assert any(name.startswith("live.sqlite.pre-restore.") for name in aside)
    assert any(name.startswith("live.sqlite-wal.pre-restore.") for name in aside)
    moved_db = next(runtime.glob("live.sqlite.pre-restore.*"))
    assert _markers(moved_db) == ["live current"]
    assert not (runtime / "dry-run.sqlite").exists(), "only the named database is restored"
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert calls == ["run --rm", "stop freqtrade", "run --rm", "up -d"]


def test_restore_single_file_uses_its_own_name(sq_home, tmp_path):
    source = Path(_backup_dir(sq_home)) / "dry-run.sqlite"

    result = _restore(sq_home, tmp_path, "--local", str(source))

    assert result.returncode == 0, result.stdout + result.stderr
    runtime = sq_home / "user_data" / "runtime"
    assert _markers(runtime / "dry-run.sqlite") == ["dry-run backup"]
    assert _markers(runtime / "live.sqlite") == ["live current"]


@pytest.mark.parametrize("name", ["../escape.sqlite", "a/b.sqlite", "live", ".hidden.sqlite"])
def test_restore_rejects_db_names_that_are_not_plain_sqlite_files(sq_home, tmp_path, name):
    result = _restore(sq_home, tmp_path, "--local", _backup_dir(sq_home), "--db", name)

    assert result.returncode != 0
    assert "--db must" in result.stdout
    assert not (tmp_path / "docker.log").exists()


def test_restore_fails_on_a_corrupt_snapshot_before_touching_the_live_database(sq_home, tmp_path):
    corrupt = Path(_backup_dir(sq_home)) / "live.sqlite"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    runtime = sq_home / "user_data" / "runtime"

    result = _restore(sq_home, tmp_path, "--local", _backup_dir(sq_home), "--db", "live.sqlite")

    assert result.returncode != 0
    # Check the WAL first: opening the database with sqlite3 discards a junk WAL.
    assert (runtime / "live.sqlite-wal").read_bytes() == b"stale wal"
    assert _markers(runtime / "live.sqlite") == ["live current"]
    assert "stop freqtrade" not in (tmp_path / "docker.log").read_text()
