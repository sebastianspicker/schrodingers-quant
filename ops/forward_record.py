"""Serialized, atomic forward reports and incident preservation. Stdlib only."""

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def atomic_write(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identity(report: dict) -> dict:
    """Bind a window to its measurement contract; never include credentials."""
    return {
        "protocol_version": report["protocol_version"],
        "pair": report["pair"],
        "timeframe": report["timeframe"],
        "window_start": report["window"]["start"],
        "source": {
            k: report["source"].get(k)
            for k in ("mode", "strategy", "strategy_sha256", "image", "config_sha256")
        },
        "notional": report["performance"]["notional"],
        "account_capital": (report.get("account") or {}).get("notional"),
        "drawdown_limit": next(
            c["threshold_pct"] for c in report["criteria"]["criteria"] if c["id"] == "F3"
        ),
    }


def publish(directory: Path, report: dict, status: int) -> int:
    """Called under the runner lock; validate before replacing latest evidence."""
    verdict = report["criteria"]["verdict"]
    if (verdict, status) not in (("CONTINUE", 0), ("STOP", 4)):
        raise ValueError("report verdict and process exit disagree")
    manifest = directory / "forward-window.json"
    unbound = report["fidelity"]["window_start_source"] == "none (no trades yet)"
    if manifest.exists() and unbound:
        raise ValueError("bound window lost its start; restore its database or declared --since")
    if not unbound:
        expected = identity(report)
        if manifest.exists() and json.loads(manifest.read_text()) != expected:
            raise ValueError(
                "window contract changed; archive the previous window before restarting"
            )
        if not manifest.exists():
            atomic_write(manifest, expected)
    latch = directory / "forward-stop.json"
    if verdict == "STOP" and not latch.exists():
        atomic_write(latch, report)
    atomic_write(directory / "forward-latest.json", report)
    return 4 if latch.exists() else status


STDERR_TAIL_LINES = 40
_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer|basic)\s+\S+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)://[^/\s:@]+:[^/\s@]+@"), "://[REDACTED]@"),
    (
        re.compile(
            r"(?i)([\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|authorization|cookie)[\w.-]*"
            r"\s*[=:]\s*)\S+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"[A-Za-z0-9_\-+/=]{32,}"), "[REDACTED]"),
)


def scrub(text: str) -> str:
    """Conservatively blank credential-looking values before logging child output."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def stderr_tail(stderr: str | bytes | None) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if not isinstance(stderr, str):
        return ""
    return "\n".join(scrub(line) for line in stderr.splitlines()[-STDERR_TAIL_LINES:])


def mark_error(directory: Path, exc: Exception) -> None:
    atomic_write(
        directory / "forward-latest.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "criteria": {"verdict": "ERROR"},
            "error": {"code": "REPORT_FAILED", "type": type(exc).__name__},
            "action": "Run make forward-report; check window manifest and market data.",
        },
    )


def run(root: Path, args: list[str]) -> int:
    directory = root / "user_data" / "runtime" / "reports"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / ".forward.lock").open("a")
    except OSError as exc:
        # No lock, so no child run: still replace a stale CONTINUE if we can,
        # and otherwise say why on one line instead of a traceback.
        try:
            mark_error(directory, exc)
            note = "latest marked ERROR"
        except OSError:
            note = "could not write an ERROR record"
        print(f"forward report failed ({type(exc).__name__}: {directory}); {note}", file=sys.stderr)
        return 1
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("forward report already running", file=sys.stderr)
            return 1
        name = f"forward-{datetime.now(UTC):%Y-%m-%dT%H%M%S%fZ}.json"
        report_path = directory / name
        child_stderr = child_stdout = None
        try:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    "-T",
                    "tools",
                    "python",
                    "-m",
                    "sq.live.forward",
                    *args,
                    "--out",
                    f"/freqtrade/user_data/runtime/reports/{name}",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=540,
                check=False,
            )
            child_stderr = getattr(result, "stderr", None)
            # On success the child's stdout is the report itself, not an error.
            if result.returncode not in (0, 4):
                child_stdout = getattr(result, "stdout", None)
            if result.returncode not in (0, 4) or not report_path.exists():
                raise RuntimeError("report process failed")
            report = json.loads(report_path.read_text())
            status = publish(directory, report, result.returncode)
            p = report["performance"]
            print(
                f"forward {report['window']['start']} -> {report['window']['end']}: "
                f"closed {p['closed_trades']['count']}, net {p['net_return_pct']:+.2f}%, "
                f"drawdown {p['mtm_max_drawdown_pct']:.2f}%, "
                f"verdict {report['criteria']['verdict']}, incident latched {status == 4}"
            )
            return status
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            subprocess.TimeoutExpired,
        ) as exc:
            mark_error(directory, exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                child_stderr = exc.stderr
                child_stdout = exc.stdout
            # The forward CLI prints its deliberate errors as JSON on stdout;
            # tracebacks and Docker errors go to stderr. Show both tails.
            for stream, text in (("stdout", child_stdout), ("stderr", child_stderr)):
                tail = stderr_tail(text)
                if tail:
                    print(
                        f"child {stream} (last {STDERR_TAIL_LINES} lines, scrubbed):",
                        file=sys.stderr,
                    )
                    print(tail, file=sys.stderr)
            print(
                f"forward report failed ({type(exc).__name__}); latest marked ERROR",
                file=sys.stderr,
            )
            return 1


if __name__ == "__main__":
    sys.exit(run(Path(__file__).resolve().parents[1], sys.argv[1:]))
