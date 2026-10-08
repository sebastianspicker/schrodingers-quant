import copy
import json
from types import SimpleNamespace

import pytest

import forward_record


def report(verdict="CONTINUE"):
    return {
        "protocol_version": 2,
        "pair": "BTC/EUR",
        "timeframe": "4h",
        "source": {"strategy": "H1ChannelBreakout", "mode": "dry_run", "strategy_sha256": "abc"},
        "window": {"start": "2026-10-08T00:00:00+00:00", "end": "2026-10-10T00:00:00+00:00"},
        "fidelity": {"window_start_source": "--since"},
        "performance": {
            "notional": 8,
            "closed_trades": {"count": 1},
            "net_return_pct": 2.0,
            "mtm_max_drawdown_pct": 1.0,
        },
        "criteria": {"verdict": verdict, "criteria": [{"id": "F3", "threshold_pct": 31.31}]},
    }


def test_stop_latch_survives_continue_and_preserves_first_incident(tmp_path):
    stop = report("STOP")
    assert forward_record.publish(tmp_path, stop, 4) == 4
    again = copy.deepcopy(stop)
    again["window"]["end"] = "2026-10-11"
    assert forward_record.publish(tmp_path, again, 4) == 4
    assert json.loads((tmp_path / "forward-stop.json").read_text()) == stop
    assert forward_record.publish(tmp_path, report(), 0) == 4
    assert (
        json.loads((tmp_path / "forward-latest.json").read_text())["criteria"]["verdict"]
        == "CONTINUE"
    )


@pytest.mark.parametrize(
    "key,value", [("notional", 10), ("strategy", "other"), ("start", "2026-11-01")]
)
def test_contract_drift_does_not_overwrite_latest(tmp_path, key, value):
    original = report()
    forward_record.publish(tmp_path, original, 0)
    changed = copy.deepcopy(original)
    location = {"notional": "performance", "strategy": "source", "start": "window"}[key]
    changed[location][key] = value
    with pytest.raises(ValueError, match="contract changed"):
        forward_record.publish(tmp_path, changed, 0)
    assert json.loads((tmp_path / "forward-latest.json").read_text()) == original


def test_failed_run_replaces_latest_with_error(tmp_path, monkeypatch):
    directory = tmp_path / "user_data/runtime/reports"
    directory.mkdir(parents=True)
    forward_record.publish(directory, report(), 0)
    monkeypatch.setattr(
        forward_record.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    assert forward_record.run(tmp_path, []) == 1
    latest = json.loads((directory / "forward-latest.json").read_text())
    assert latest["criteria"]["verdict"] == "ERROR"
    assert "generated_at" in latest


@pytest.mark.parametrize("start", ["2026-10-01T00:00:00+00:00", "2026-10-08T00:00:00+00:00"])
def test_empty_restored_database_cannot_bypass_bound_window(tmp_path, monkeypatch, start):
    directory = tmp_path / "user_data/runtime/reports"
    directory.mkdir(parents=True)
    original = report()
    forward_record.publish(directory, original, 0)
    manifest = (directory / "forward-window.json").read_bytes()
    changed = report()
    changed["fidelity"]["window_start_source"] = "none (no trades yet)"
    changed["window"]["start"] = start
    changed["performance"]["closed_trades"]["count"] = 0

    def docker(command, **kwargs):
        target = directory / command[-1].split("/")[-1]
        target.write_text(json.dumps(changed))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(forward_record.subprocess, "run", docker)
    assert forward_record.run(tmp_path, []) == 1
    assert (directory / "forward-window.json").read_bytes() == manifest
    latest = json.loads((directory / "forward-latest.json").read_text())
    assert latest["criteria"]["verdict"] == "ERROR"


def test_runner_passes_literal_args_and_publishes_output(tmp_path, monkeypatch):
    def docker(command, **kwargs):
        assert command[-4:-2] == ["--since", "2026-10-08"]
        filename = command[-1].split("/")[-1]
        target = tmp_path / "user_data/runtime/reports" / filename
        target.write_text(json.dumps(report()))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(forward_record.subprocess, "run", docker)
    assert forward_record.run(tmp_path, ["--since", "2026-10-08"]) == 0
    assert (tmp_path / "user_data/runtime/reports/forward-latest.json").exists()


def test_exit_verdict_disagreement_fails(tmp_path):
    with pytest.raises(ValueError, match="disagree"):
        forward_record.publish(tmp_path, report("STOP"), 0)
    assert not (tmp_path / "forward-latest.json").exists()
