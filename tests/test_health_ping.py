"""Tests for ops/health_ping.py: pure decisions and the orchestration with I/O patched."""

import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

import health_ping as hp
from health_ping import Decision

NOW = 1_000_000.0


# --- pure logic --------------------------------------------------------------------


def test_parse_health_payload():
    assert hp.parse_health_payload({"last_process_ts": 123}) == 123
    assert hp.parse_health_payload({"last_process_ts": 123.9}) == 123
    assert hp.parse_health_payload({"last_process_ts": None}) is None
    assert hp.parse_health_payload({}) is None


def test_decide_bot_health_missing_ts():
    d = hp.decide_bot_health(None, NOW, 900)
    assert not d.healthy and "no last_process_ts" in d.reason


def test_decide_bot_health_future_ts():
    d = hp.decide_bot_health(int(NOW) + 60, NOW, 900)
    assert not d.healthy and "future" in d.reason and "clock skew" in d.reason


def test_decide_bot_health_stale_fresh_and_boundary():
    stale = hp.decide_bot_health(int(NOW) - 901, NOW, 900)
    assert not stale.healthy and "901s ago" in stale.reason
    assert hp.decide_bot_health(int(NOW) - 900, NOW, 900).healthy  # exactly at the limit
    fresh = hp.decide_bot_health(int(NOW) - 10, NOW, 900)
    assert fresh == Decision(True, "last process was 10s ago")


def test_decide_disk_health():
    assert hp.decide_disk_health(50.0, 85.0) == Decision(True, "disk usage 50.0%")
    assert not hp.decide_disk_health(85.0, 85.0).healthy  # at threshold fails
    assert "threshold" in hp.decide_disk_health(90.0, 85.0).reason


def test_combine_decisions():
    ok1, ok2 = Decision(True, "a"), Decision(True, "b")
    bad1, bad2 = Decision(False, "x"), Decision(False, "y")
    assert hp.combine_decisions(ok1, ok2) == Decision(True, "a; b")
    assert hp.combine_decisions(ok1, bad1) == Decision(False, "x")
    assert hp.combine_decisions(bad1, ok1, bad2) == Decision(False, "x; y")


# --- run() ---------------------------------------------------------------------------

URL = "https://hc.example/ping/abc"


class Pings(list):
    """Recorded (url, body) pings; `deliver` controls what send_ping reports."""

    deliver = True


@pytest.fixture
def pings(monkeypatch):
    sent = Pings()

    def fake_send(url, timeout, body=""):
        sent.append((url, body))
        return sent.deliver

    monkeypatch.setattr(hp, "send_ping", fake_send)
    monkeypatch.setattr(hp.time, "time", lambda: NOW)
    return sent


def call_run(**overrides):
    kwargs = dict(
        base_url="http://127.0.0.1:8080",
        username="u",
        password="p",
        hc_ping_url=URL,
        max_age_seconds=900,
        disk_path=Path("."),
        disk_max_percent=85.0,
        timeout=1.0,
    )
    return hp.run(**{**kwargs, **overrides})


def patch_health(monkeypatch, payload=None, error=None, disk=10.0):
    def fake_fetch(base_url, username, password, timeout):
        if error is not None:
            raise error
        return payload

    monkeypatch.setattr(hp, "fetch_health", fake_fetch)
    monkeypatch.setattr(hp, "disk_usage_percent", lambda path: disk)


def test_run_ok(monkeypatch, pings):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    assert call_run() == hp.EXIT_OK
    assert pings == [(URL, "")]


def test_run_ok_but_ping_not_delivered(monkeypatch, pings):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    pings.deliver = False
    assert call_run() == hp.EXIT_PING_FAILED


def test_run_unhealthy_stale_bot(monkeypatch, pings):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5000})
    assert call_run() == hp.EXIT_UNHEALTHY
    ((url, body),) = pings
    assert url == URL + "/fail"
    assert "exceeds 900s threshold" in body


def test_run_unhealthy_disk_and_trailing_slash(monkeypatch, pings):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5}, disk=95.0)
    assert call_run(hc_ping_url=URL + "/") == hp.EXIT_UNHEALTHY
    ((url, body),) = pings
    assert url == URL + "/fail"
    assert "disk usage 95.0%" in body


def test_run_failure_body_does_not_include_exception_secrets(monkeypatch, pings, caplog):
    secret = "private-api-password"
    patch_health(monkeypatch, None, error=urllib.error.URLError(secret * 50))
    assert call_run() == hp.EXIT_UNREACHABLE
    ((_, body),) = pings
    assert len(body) <= 200
    assert secret not in body and secret not in caplog.text


def test_run_unreachable(monkeypatch, pings):
    patch_health(monkeypatch, error=urllib.error.URLError("connection refused"))
    assert call_run() == hp.EXIT_UNREACHABLE
    ((url, body),) = pings
    assert url == URL + "/fail"
    assert "could not read bot health" in body and "URLError" in body


def test_run_unreachable_and_ping_fails(monkeypatch, pings):
    patch_health(monkeypatch, error=OSError("down"))
    pings.deliver = False
    assert call_run() == hp.EXIT_PING_FAILED


def test_run_unhealthy_and_ping_fails(monkeypatch, pings):
    patch_health(monkeypatch, {})
    pings.deliver = False
    assert call_run() == hp.EXIT_PING_FAILED


def test_run_invalid_json_counts_as_unreachable(monkeypatch, pings):
    patch_health(monkeypatch, error=ValueError("bad json"))
    assert call_run() == hp.EXIT_UNREACHABLE


# --- main() ----------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "HC_PING_URL",
        "FREQTRADE_API_USERNAME",
        "FREQTRADE_API_PASSWORD",
        "FREQTRADE_API_URL",
        "HEALTH_MAX_AGE_SECONDS",
        "HEALTH_DISK_PATH",
        "HEALTH_DISK_MAX_PERCENT",
        "HEALTH_TIMEOUT_SECONDS",
        "HEALTH_FORWARD_REPORT",
        "HEALTH_FORWARD_MAX_AGE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_main_without_ping_url(monkeypatch, clean_env, pings):
    assert hp.main([]) == hp.EXIT_PING_FAILED
    assert pings == []


def test_main_fail_reason_posts_to_fail(monkeypatch, clean_env, pings):
    monkeypatch.setenv("HC_PING_URL", URL)
    assert hp.main(["--fail", "unit foo failed"]) == hp.EXIT_OK
    assert pings == [(URL + "/fail", "unit foo failed")]


def test_main_fail_reason_delivery_failure(monkeypatch, clean_env, pings):
    monkeypatch.setenv("HC_PING_URL", URL)
    pings.deliver = False
    assert hp.main(["--fail", "boom"]) == hp.EXIT_PING_FAILED


def test_main_missing_api_credentials(monkeypatch, clean_env, pings):
    monkeypatch.setenv("HC_PING_URL", URL)
    assert hp.main([]) == hp.EXIT_UNREACHABLE
    assert pings == [(URL + "/fail", "missing API credentials")]


def test_main_runs_a_cycle(monkeypatch, clean_env, pings, tmp_path):
    monkeypatch.setenv("HC_PING_URL", URL)
    monkeypatch.setenv("FREQTRADE_API_USERNAME", "u")
    monkeypatch.setenv("FREQTRADE_API_PASSWORD", "p")
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    assert hp.main(["--disk-path", str(tmp_path)]) == hp.EXIT_OK
    assert pings == [(URL, "")]


def report_payload(age=0, verdict="CONTINUE"):
    return {
        "generated_at": datetime.fromtimestamp(NOW - age, UTC).isoformat(),
        "criteria": {"verdict": verdict},
    }


def test_forward_health_fresh_boundary_stale_and_future():
    assert hp.decide_forward_health(report_payload(), NOW, 100).healthy
    assert hp.decide_forward_health(report_payload(age=100), NOW, 100).healthy
    assert not hp.decide_forward_health(report_payload(age=101), NOW, 100).healthy
    assert not hp.decide_forward_health(report_payload(age=-1), NOW, 100).healthy


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": "generation failed"},
        {"criteria": []},
        {"criteria": {"verdict": "UNKNOWN"}},
        {"criteria": {"verdict": "CONTINUE"}},
        {"criteria": {"verdict": "CONTINUE"}, "generated_at": "malformed"},
        {"criteria": {"verdict": "CONTINUE"}, "generated_at": "1970-01-12T13:46:40"},
    ],
)
def test_forward_health_fails_closed_on_malformed_report(payload):
    assert not hp.decide_forward_health(payload, NOW, 100).healthy


def test_forward_health_stop_never_cleared_by_fresh_timestamp():
    result = hp.decide_forward_health(report_payload(verdict="STOP"), NOW, 100)
    assert not result.healthy and "STOP" in result.reason


@pytest.mark.parametrize("contents", [None, "{not json", "\udcff"])
def test_forward_report_unavailable_or_unreadable(tmp_path, contents):
    path = tmp_path / "latest.json"
    if contents is not None:
        path.write_bytes(contents.encode("utf-8", errors="surrogateescape"))
    assert not hp.forward_report_health(path, NOW, 100).healthy


def test_run_forward_stop_keeps_repeated_heartbeat_failed(monkeypatch, pings, tmp_path):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(report_payload(verdict="STOP")))
    assert call_run(forward_report=path) == hp.EXIT_UNHEALTHY
    assert call_run(forward_report=path) == hp.EXIT_UNHEALTHY
    assert all(url == URL + "/fail" and "STOP" in body for url, body in pings)
    path.write_text(json.dumps(report_payload()))
    assert call_run(forward_report=path) == hp.EXIT_OK
    assert pings[-1] == (URL, "")


def test_stop_latch_outlives_continue_report(monkeypatch, pings, tmp_path):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    path = tmp_path / "forward-latest.json"
    path.write_text(json.dumps(report_payload()))
    latch = tmp_path / "forward-stop.json"
    # Presence is enough: even an incomplete or malformed incident record
    # must keep the monitor failed until an operator handles it.
    latch.write_text("partial STOP record")
    assert call_run(forward_report=path) == hp.EXIT_UNHEALTHY
    assert "STOP latch exists" in pings[-1][1]
    assert call_run(forward_report=path) == hp.EXIT_UNHEALTHY
    latch.rename(tmp_path / "archived-incident.json")
    assert call_run(forward_report=path) == hp.EXIT_OK
    assert pings[-1] == (URL, "")


def test_main_forward_report_env_and_staleness(monkeypatch, clean_env, pings, tmp_path):
    monkeypatch.setenv("HC_PING_URL", URL)
    monkeypatch.setenv("FREQTRADE_API_USERNAME", "u")
    monkeypatch.setenv("FREQTRADE_API_PASSWORD", "p")
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(report_payload(age=61)))
    monkeypatch.setenv("HEALTH_FORWARD_REPORT", str(path))
    monkeypatch.setenv("HEALTH_FORWARD_MAX_AGE_SECONDS", "60")
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})
    assert hp.main([]) == hp.EXIT_UNHEALTHY
    assert "exceeds 60s threshold" in pings[-1][1]


def test_ping_delivery_exception_does_not_log_secret(monkeypatch, caplog):
    secret = "secret-ping-token"

    def fail(*args, **kwargs):
        raise urllib.error.URLError("https://monitor.example/" + secret)

    monkeypatch.setattr(hp.urllib.request, "urlopen", fail)
    assert not hp.send_ping("https://monitor.example/" + secret, 1)
    assert secret not in caplog.text and "URLError" in caplog.text


def test_ping_malformed_url_does_not_log_secret(caplog):
    assert not hp.send_ping("private-token", 1)
    assert "private-token" not in caplog.text and "ValueError" in caplog.text


def test_main_rejects_nonpositive_forward_age(clean_env):
    with pytest.raises(SystemExit) as exc:
        hp.main(["--forward-max-age-seconds", "0"])
    assert exc.value.code == 2


@pytest.mark.parametrize("payload", [[], {"last_process_ts": "secret-invalid-value"}])
def test_run_malformed_health_payload_alerts(monkeypatch, pings, payload):
    patch_health(monkeypatch, payload)
    assert call_run() == hp.EXIT_UNHEALTHY
    assert pings == [(URL + "/fail", "bot health payload is malformed")]


def test_run_disk_failure_alerts_without_path(monkeypatch, pings):
    patch_health(monkeypatch, {"last_process_ts": int(NOW) - 5})

    def fail(path):
        raise OSError("private-path")

    monkeypatch.setattr(hp, "disk_usage_percent", fail)
    assert call_run() == hp.EXIT_UNHEALTHY
    assert "OSError" in pings[-1][1] and "private-path" not in pings[-1][1]
