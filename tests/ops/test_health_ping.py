"""Unit tests for ops/health_ping.py's parse/decide logic and ping routing.

No network access: all I/O functions (fetch_health, send_ping,
disk_usage_percent) are monkeypatched.
"""

import urllib.error

import pytest

import health_ping

# --- Pure logic: parse_health_payload ---


def test_parse_health_payload_present():
    assert health_ping.parse_health_payload({"last_process_ts": 1700000000}) == 1700000000


def test_parse_health_payload_missing_field():
    assert health_ping.parse_health_payload({"last_process": None, "last_process_ts": None}) is None


def test_parse_health_payload_empty_dict():
    assert health_ping.parse_health_payload({}) is None


# --- Pure logic: decide_bot_health ---


def test_decide_bot_health_healthy_when_recent():
    decision = health_ping.decide_bot_health(1000, now_ts=1000 + 60, max_age_seconds=900)
    assert decision.healthy
    assert "60s ago" in decision.reason


def test_decide_bot_health_healthy_at_exact_threshold():
    decision = health_ping.decide_bot_health(1000, now_ts=1000 + 900, max_age_seconds=900)
    assert decision.healthy


def test_decide_bot_health_stale_past_threshold():
    decision = health_ping.decide_bot_health(1000, now_ts=1000 + 901, max_age_seconds=900)
    assert not decision.healthy
    assert "901s ago" in decision.reason


def test_decide_bot_health_missing_timestamp():
    decision = health_ping.decide_bot_health(None, now_ts=1000, max_age_seconds=900)
    assert not decision.healthy
    assert "never completed a cycle" in decision.reason


def test_decide_bot_health_future_timestamp_is_clock_skew():
    decision = health_ping.decide_bot_health(2000, now_ts=1000, max_age_seconds=900)
    assert not decision.healthy
    assert "clock skew" in decision.reason


# --- Pure logic: decide_disk_health ---


def test_decide_disk_health_ok():
    decision = health_ping.decide_disk_health(50.0, max_percent=85.0)
    assert decision.healthy


def test_decide_disk_health_full():
    decision = health_ping.decide_disk_health(90.0, max_percent=85.0)
    assert not decision.healthy
    assert "90.0%" in decision.reason


def test_decide_disk_health_at_exact_threshold_fails():
    # ">= max_percent" fails: the threshold itself is already "full enough".
    decision = health_ping.decide_disk_health(85.0, max_percent=85.0)
    assert not decision.healthy


# --- Pure logic: combine_decisions ---


def test_combine_decisions_all_healthy():
    combined = health_ping.combine_decisions(
        health_ping.Decision(True, "bot ok"), health_ping.Decision(True, "disk ok")
    )
    assert combined.healthy
    assert "bot ok" in combined.reason
    assert "disk ok" in combined.reason


def test_combine_decisions_one_unhealthy():
    combined = health_ping.combine_decisions(
        health_ping.Decision(True, "bot ok"), health_ping.Decision(False, "disk full")
    )
    assert not combined.healthy
    assert combined.reason == "disk full"


# --- run(): I/O orchestration, with fetch_health/send_ping/disk_usage_percent monkeypatched ---


@pytest.fixture
def base_kwargs(tmp_path):
    return {
        "base_url": "http://127.0.0.1:8080",
        "username": "user",
        "password": "pass",
        "hc_ping_url": "http://example.invalid/ping/abc",
        "max_age_seconds": 900,
        "disk_path": tmp_path,
        "disk_max_percent": 85.0,
        "timeout": 1.0,
    }


def test_run_healthy_pings_success_url(monkeypatch, base_kwargs):
    now = 1_700_000_000
    monkeypatch.setattr(health_ping.time, "time", lambda: now)
    monkeypatch.setattr(health_ping, "fetch_health", lambda *a, **kw: {"last_process_ts": now - 60})
    monkeypatch.setattr(health_ping, "disk_usage_percent", lambda path: 10.0)

    pinged = {}

    def fake_send_ping(url, timeout, body=""):
        pinged["url"] = url
        pinged["body"] = body
        return True

    monkeypatch.setattr(health_ping, "send_ping", fake_send_ping)

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_OK
    assert pinged["url"] == base_kwargs["hc_ping_url"]
    assert pinged["body"] == ""


def test_run_stale_pings_fail_url_with_reason(monkeypatch, base_kwargs):
    now = 1_700_000_000
    monkeypatch.setattr(health_ping.time, "time", lambda: now)
    monkeypatch.setattr(
        health_ping, "fetch_health", lambda *a, **kw: {"last_process_ts": now - 3600}
    )
    monkeypatch.setattr(health_ping, "disk_usage_percent", lambda path: 10.0)

    pinged = {}

    def fake_send_ping(url, timeout, body=""):
        pinged["url"] = url
        pinged["body"] = body
        return True

    monkeypatch.setattr(health_ping, "send_ping", fake_send_ping)

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_UNHEALTHY
    assert pinged["url"] == base_kwargs["hc_ping_url"] + "/fail"
    assert "exceeds" in pinged["body"]


def test_run_missing_fields_is_unhealthy(monkeypatch, base_kwargs):
    monkeypatch.setattr(health_ping.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(health_ping, "fetch_health", lambda *a, **kw: {})
    monkeypatch.setattr(health_ping, "disk_usage_percent", lambda path: 10.0)

    pinged = {}
    monkeypatch.setattr(
        health_ping,
        "send_ping",
        lambda url, timeout, body="": pinged.update(url=url, body=body) or True,
    )

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_UNHEALTHY
    assert "never completed a cycle" in pinged["body"]


def test_run_unreachable_api_pings_fail_url(monkeypatch, base_kwargs):
    def raise_connection_error(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(health_ping, "fetch_health", raise_connection_error)

    pinged = {}
    monkeypatch.setattr(
        health_ping,
        "send_ping",
        lambda url, timeout, body="": pinged.update(url=url, body=body) or True,
    )

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_UNREACHABLE
    assert pinged["url"] == base_kwargs["hc_ping_url"] + "/fail"
    assert "could not reach" in pinged["body"]


def test_run_disk_full_pings_fail_url(monkeypatch, base_kwargs):
    now = 1_700_000_000
    monkeypatch.setattr(health_ping.time, "time", lambda: now)
    monkeypatch.setattr(health_ping, "fetch_health", lambda *a, **kw: {"last_process_ts": now - 60})
    monkeypatch.setattr(health_ping, "disk_usage_percent", lambda path: 99.0)

    pinged = {}
    monkeypatch.setattr(
        health_ping,
        "send_ping",
        lambda url, timeout, body="": pinged.update(url=url, body=body) or True,
    )

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_UNHEALTHY
    assert "disk usage" in pinged["body"]


def test_run_ping_delivery_failure_is_reported_distinctly(monkeypatch, base_kwargs):
    now = 1_700_000_000
    monkeypatch.setattr(health_ping.time, "time", lambda: now)
    monkeypatch.setattr(health_ping, "fetch_health", lambda *a, **kw: {"last_process_ts": now - 60})
    monkeypatch.setattr(health_ping, "disk_usage_percent", lambda path: 10.0)
    monkeypatch.setattr(health_ping, "send_ping", lambda url, timeout, body="": False)

    exit_code = health_ping.run(**base_kwargs)

    assert exit_code == health_ping.EXIT_PING_FAILED


# --- disk_usage_percent: real filesystem call, still no network ---


def test_disk_usage_percent_returns_plausible_fraction(tmp_path):
    used_percent = health_ping.disk_usage_percent(tmp_path)
    assert 0.0 <= used_percent <= 100.0


# --- main(): --fail mode used by the sq-alert@.service OnFailure= hook ---


def test_main_fail_mode_sends_reason_and_returns_ok(monkeypatch):
    monkeypatch.setenv("HC_PING_URL", "http://example.invalid/ping/abc")
    pinged = {}
    monkeypatch.setattr(
        health_ping,
        "send_ping",
        lambda url, timeout, body="": pinged.update(url=url, body=body) or True,
    )

    exit_code = health_ping.main(["--fail", "sq-backup.service failed"])

    assert exit_code == health_ping.EXIT_OK
    assert pinged["url"] == "http://example.invalid/ping/abc/fail"
    assert pinged["body"] == "sq-backup.service failed"


def test_main_fail_mode_without_hc_ping_url_fails_fast(monkeypatch):
    monkeypatch.delenv("HC_PING_URL", raising=False)

    exit_code = health_ping.main(["--fail", "sq-backup.service failed"])

    assert exit_code == health_ping.EXIT_PING_FAILED


def test_main_fail_mode_ping_delivery_failure(monkeypatch):
    monkeypatch.setenv("HC_PING_URL", "http://example.invalid/ping/abc")
    monkeypatch.setattr(health_ping, "send_ping", lambda url, timeout, body="": False)

    exit_code = health_ping.main(["--fail", "sq-backup.service failed"])

    assert exit_code == health_ping.EXIT_PING_FAILED
