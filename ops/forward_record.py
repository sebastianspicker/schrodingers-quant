"""Serialized, atomic forward reports and incident preservation. Stdlib only."""

import fcntl
import json
import os
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


def run(root: Path, args: list[str]) -> int:
    directory = root / "user_data" / "runtime" / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".forward.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("forward report already running", file=sys.stderr)
            return 1
        name = f"forward-{datetime.now(UTC):%Y-%m-%dT%H%M%S%fZ}.json"
        report_path = directory / name
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
            atomic_write(
                directory / "forward-latest.json",
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "criteria": {"verdict": "ERROR"},
                    "error": {"code": "REPORT_FAILED", "type": type(exc).__name__},
                    "action": "Run make forward-report; check window manifest and market data.",
                },
            )
            print(
                f"forward report failed ({type(exc).__name__}); latest marked ERROR",
                file=sys.stderr,
            )
            return 1


if __name__ == "__main__":
    sys.exit(run(Path(__file__).resolve().parents[1], sys.argv[1:]))
