"""Host-side dead-man's-switch health check for the Freqtrade dry-run container.

Meant to run every 5 minutes from a systemd timer on the Debian 13 host (see
ops/systemd/sq-health.timer). Queries the Freqtrade REST API's
/api/v1/health endpoint over the loopback interface, decides whether the bot's
last processing cycle is recent enough and whether the host disk has room, and
reports the result to an external dead-man's-switch (a healthchecks.io-compatible
ping URL): a plain GET on success, a GET/POST to "<url>/fail" with a short
reason otherwise. Silence at the dead-man's-switch, not just a local alert, is
what catches a host or network outage that stops this script from running at all.

Credentials are read from the environment only (never from argv, so they do not
appear in `ps`), typically sourced by systemd from an EnvironmentFile such as
/etc/schrodingers-quant/ops.env (mode 600). Nothing here ever prints a secret.

Required environment variables:
  HC_PING_URL             base ping URL for the dead-man's-switch
  FREQTRADE_API_USERNAME  API server basic-auth username
  FREQTRADE_API_PASSWORD  API server basic-auth password

Optional environment variables (all have defaults, see --help):
  FREQTRADE_API_URL, HEALTH_MAX_AGE_SECONDS, HEALTH_DISK_PATH,
  HEALTH_DISK_MAX_PERCENT, HEALTH_TIMEOUT_SECONDS, SQ_HOME
"""

import argparse
import base64
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("health_ping")

# Exit codes are meaningful so the systemd unit's OnFailure= alert hook and any
# operator reading `systemctl status` can tell the failure modes apart.
EXIT_OK = 0
EXIT_UNHEALTHY = 1  # bot heartbeat stale/missing, or disk usage over threshold
EXIT_UNREACHABLE = 2  # could not reach the Freqtrade API at all
EXIT_PING_FAILED = 3  # the check ran, but the dead-man's-switch itself was unreachable

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MAX_AGE_SECONDS = 15 * 60
DEFAULT_DISK_MAX_PERCENT = 85.0
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Decision:
    healthy: bool
    reason: str


def repo_root() -> Path:
    """Repository root: SQ_HOME env var if set, else two levels above this file."""
    if sq_home := os.environ.get("SQ_HOME"):
        return Path(sq_home)
    return Path(__file__).resolve().parent.parent


# --- Pure logic (unit-testable without network or disk access) ---


def parse_health_payload(payload: dict) -> int | None:
    """Extract last_process_ts (epoch seconds) from a /api/v1/health response.

    Returns None if the field is missing or null, e.g. the bot has not yet
    completed a processing cycle since it started.
    """
    ts = payload.get("last_process_ts")
    return None if ts is None else int(ts)


def decide_bot_health(last_process_ts: int | None, now_ts: float, max_age_seconds: int) -> Decision:
    """Is the bot's last processing cycle recent enough."""
    if last_process_ts is None:
        return Decision(
            False, "health response has no last_process_ts (bot never completed a cycle)"
        )
    age = now_ts - last_process_ts
    if age < 0:
        # Clock skew between host and container: don't silently accept a
        # timestamp that claims to be in the future.
        return Decision(False, f"last_process_ts is {abs(age):.0f}s in the future (clock skew)")
    if age > max_age_seconds:
        return Decision(
            False, f"last process was {age:.0f}s ago, exceeds {max_age_seconds}s threshold"
        )
    return Decision(True, f"last process was {age:.0f}s ago")


def decide_disk_health(used_percent: float, max_percent: float) -> Decision:
    """Is disk usage below the configured threshold."""
    if used_percent >= max_percent:
        return Decision(False, f"disk usage {used_percent:.1f}% >= {max_percent:.1f}% threshold")
    return Decision(True, f"disk usage {used_percent:.1f}%")


def combine_decisions(*decisions: Decision) -> Decision:
    """Unhealthy if any input is unhealthy; reasons are joined for the report."""
    unhealthy = [d for d in decisions if not d.healthy]
    if unhealthy:
        return Decision(False, "; ".join(d.reason for d in unhealthy))
    return Decision(True, "; ".join(d.reason for d in decisions))


# --- I/O ---


def fetch_health(base_url: str, username: str, password: str, timeout: float) -> dict:
    """GET /api/v1/health with HTTP basic auth. Raises on network/HTTP error."""
    url = base_url.rstrip("/") + "/api/v1/health"
    request = urllib.request.Request(url, method="GET")
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    request.add_header("Authorization", f"Basic {credentials}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def disk_usage_percent(path: Path) -> float:
    """Percent of the filesystem holding `path` currently in use."""
    usage = shutil.disk_usage(path)
    return (usage.used / usage.total) * 100


def send_ping(url: str, timeout: float, body: str = "") -> bool:
    """Notify a healthchecks.io-style ping URL. GET for a plain success ping,
    POST with `body` for a failure report. Returns whether delivery succeeded."""
    data = body.encode("utf-8") if body else None
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError) as error:
        logger.warning("could not deliver ping to dead-man's-switch: %s", error)
        return False


def run(
    *,
    base_url: str,
    username: str,
    password: str,
    hc_ping_url: str,
    max_age_seconds: int,
    disk_path: Path,
    disk_max_percent: float,
    timeout: float,
) -> int:
    """Perform one health-check cycle and notify the dead-man's-switch."""
    now_ts = time.time()

    try:
        payload = fetch_health(base_url, username, password, timeout)
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = f"could not reach {base_url}/api/v1/health: {error}"
        logger.error(reason)
        if send_ping(hc_ping_url.rstrip("/") + "/fail", timeout, body=reason[:200]):
            return EXIT_UNREACHABLE
        return EXIT_PING_FAILED

    last_process_ts = parse_health_payload(payload)
    bot_decision = decide_bot_health(last_process_ts, now_ts, max_age_seconds)
    disk_decision = decide_disk_health(disk_usage_percent(disk_path), disk_max_percent)
    decision = combine_decisions(bot_decision, disk_decision)

    if decision.healthy:
        logger.info(decision.reason)
        if send_ping(hc_ping_url, timeout):
            return EXIT_OK
        return EXIT_PING_FAILED

    logger.warning(decision.reason)
    if send_ping(hc_ping_url.rstrip("/") + "/fail", timeout, body=decision.reason[:200]):
        return EXIT_UNHEALTHY
    return EXIT_PING_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("FREQTRADE_API_URL", DEFAULT_BASE_URL),
        help="Freqtrade API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=int(os.environ.get("HEALTH_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS)),
        help="Maximum age of last_process before the bot is considered stale",
    )
    disk_path_env = os.environ.get("HEALTH_DISK_PATH")
    parser.add_argument(
        "--disk-path",
        type=Path,
        default=Path(disk_path_env) if disk_path_env else repo_root(),
        help="Path on the filesystem to disk-check (default: the repository root)",
    )
    parser.add_argument(
        "--disk-max-percent",
        type=float,
        default=float(os.environ.get("HEALTH_DISK_MAX_PERCENT", DEFAULT_DISK_MAX_PERCENT)),
        help="Disk usage percent at or above which the check fails",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("HEALTH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
        help="HTTP timeout in seconds for both the API and the ping URL",
    )
    parser.add_argument(
        "--fail",
        metavar="REASON",
        default=None,
        help=(
            "Skip the health/disk checks; just report REASON to HC_PING_URL/fail and exit. "
            "Used by systemd OnFailure= hooks (see ops/systemd/sq-alert@.service) "
            "to relay an unrelated unit's failure to the same dead-man's-switch."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    hc_ping_url = os.environ.get("HC_PING_URL", "")

    if not hc_ping_url:
        logger.error("HC_PING_URL is not set; cannot report to the dead-man's-switch")
        return EXIT_PING_FAILED

    if args.fail is not None:
        logger.warning("reporting failure: %s", args.fail)
        if send_ping(hc_ping_url.rstrip("/") + "/fail", args.timeout, body=args.fail[:200]):
            return EXIT_OK
        return EXIT_PING_FAILED

    username = os.environ.get("FREQTRADE_API_USERNAME", "")
    password = os.environ.get("FREQTRADE_API_PASSWORD", "")
    if not username or not password:
        logger.error("FREQTRADE_API_USERNAME/FREQTRADE_API_PASSWORD are not set")
        send_ping(hc_ping_url.rstrip("/") + "/fail", args.timeout, body="missing API credentials")
        return EXIT_UNREACHABLE

    return run(
        base_url=args.base_url,
        username=username,
        password=password,
        hc_ping_url=hc_ping_url,
        max_age_seconds=args.max_age_seconds,
        disk_path=args.disk_path,
        disk_max_percent=args.disk_max_percent,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
