"""The marked-to-market record a backtest step writes: its candle window and
provenance. The window rule reproduces the recorded train, validation,
sensitivity and ETH `mtm-*.json` files (H1's period dates are inclusive, so
the window's exclusive bound is the day after the period's last day)."""

import dataclasses
import json

import pandas as pd

from sq.research import h1, pipeline


def test_mtm_record_marks_through_the_last_day_and_carries_provenance(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / ".last_result.json").write_text(json.dumps({"latest_backtest": "bt.zip"}))
    strategy_file = tmp_path / "Strategy.py"
    strategy_file.write_text("class Strategy: ...\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n")
    monkeypatch.setenv("SQ_FREQTRADE_IMAGE", "example/image@sha256:abc")

    dates = pd.date_range("2022-12-30", "2023-01-01 20:00", freq="4h", tz="UTC")
    candles = pd.DataFrame({"date": dates, "close": 100.0})
    monkeypatch.setattr(pipeline, "load_pair_history", lambda **kwargs: candles)
    monkeypatch.setattr(pipeline, "load_backtest_data", lambda path: pd.DataFrame())
    windows = []

    def fake_mtm_report(**kwargs):
        windows.append(kwargs["candles"])
        return {"start": kwargs["start"], "end": kwargs["end"]}

    monkeypatch.setattr(pipeline.metrics, "mtm_report", fake_mtm_report)
    run = dataclasses.replace(h1.TRAIN_RUNS[0], strategy_file=strategy_file)

    pipeline.write_mtm(run, results_dir, record_dir=tmp_path, manifest_path=manifest)

    window = windows[0]["date"]
    assert window.min() == pd.Timestamp("2022-12-30", tz="UTC")
    assert window.max() == pd.Timestamp("2022-12-31 20:00", tz="UTC")
    record = json.loads((tmp_path / "mtm-train-base-BTC_EUR.json").read_text())
    assert (record["start"], record["end"]) == ("2020-01-01", "2023-01-01")
    assert record["provenance"]["image_digest"] == "example/image@sha256:abc"
    assert record["provenance"]["strategy_file"] == str(strategy_file)
    assert len(record["provenance"]["strategy_file_sha256"]) == 64
    assert len(record["provenance"]["data_manifest_sha256"]) == 64
