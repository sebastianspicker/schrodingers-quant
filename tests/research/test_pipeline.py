"""Characterization tests for sq.research.pipeline: the exact Freqtrade argv
each step builds, derived from research/run.sh before its removal (the
behavioral contract this migration preserves), not from pipeline.py's own
code. subprocess.run and the summary/mtm writers are stubbed so no real
Freqtrade process runs and nothing is written to disk; Path.mkdir is stubbed
too so the default (container) paths used in the asserted argv never touch
the real filesystem.
"""

import subprocess
from pathlib import Path

import pytest

from sq.research import pipeline

CONFIG = "/freqtrade/research/configs/backtest.json"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: None)
    monkeypatch.setattr(pipeline, "write_summary", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "write_mtm", lambda *a, **k: None)


def _capture_argv(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv, check=False):
        assert check is True
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    return calls


def test_train_argv(monkeypatch):
    calls = _capture_argv(monkeypatch)

    pipeline.train()

    assert calls == [
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--strategy-path",
            "/freqtrade/strategies",
            "--timerange",
            "20200101-20221231",
            "--fee",
            "0.005",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/train-base-BTC_EUR",
        ],
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--strategy-path",
            "/freqtrade/strategies",
            "--timerange",
            "20200101-20221231",
            "--fee",
            "0.01",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/train-stress-BTC_EUR",
        ],
    ]


def test_sensitivity_argv(monkeypatch):
    calls = _capture_argv(monkeypatch)

    pipeline.sensitivity()

    assert calls == [
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1Sensitivity10x5",
            "--strategy-path",
            "/freqtrade/research/strategies",
            "--timerange",
            "20200101-20240630",
            "--fee",
            "0.005",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/sensitivity-10x5-BTC_EUR",
        ],
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1Sensitivity55x20",
            "--strategy-path",
            "/freqtrade/research/strategies",
            "--timerange",
            "20200101-20240630",
            "--fee",
            "0.005",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/sensitivity-55x20-BTC_EUR",
        ],
    ]


def test_eth_robustness_argv(monkeypatch):
    calls = _capture_argv(monkeypatch)

    pipeline.eth_robustness()

    assert calls == [
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--strategy-path",
            "/freqtrade/strategies",
            "--timerange",
            "20200101-20240630",
            "--fee",
            "0.005",
            "--pairs",
            "ETH/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/eth-robustness-train-validation",
        ],
    ]


def test_heldout_argv(monkeypatch, capsys):
    calls = _capture_argv(monkeypatch)

    pipeline.heldout()

    assert calls == [
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--strategy-path",
            "/freqtrade/strategies",
            "--timerange",
            "20240701-20260920",
            "--fee",
            "0.005",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/heldout-base-BTC_EUR",
        ],
        [
            "freqtrade",
            "backtesting",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--strategy-path",
            "/freqtrade/strategies",
            "--timerange",
            "20240701-20260920",
            "--fee",
            "0.01",
            "--pairs",
            "BTC/EUR",
            "--export",
            "trades",
            "--backtest-directory",
            "/freqtrade/user_data/backtest_results/H1/heldout-stress-BTC_EUR",
        ],
    ]
    # The held-out warning: printed before the runs, once, per H1.md.
    assert "predeclared held-out" in capsys.readouterr().err


def test_bias_argv(monkeypatch):
    calls = _capture_argv(monkeypatch)

    pipeline.bias()

    assert calls == [
        [
            "freqtrade",
            "lookahead-analysis",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--timerange",
            "20200110-20240630",
            "--pairs",
            "BTC/EUR",
        ],
        [
            "freqtrade",
            "recursive-analysis",
            "--config",
            CONFIG,
            "--strategy",
            "H1ChannelBreakout",
            "--timerange",
            "20200110-20240630",
            "--pairs",
            "BTC/EUR",
        ],
    ]
