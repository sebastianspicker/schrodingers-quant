"""Tests for sq.research.equity_curves: the drift guard that refuses to write
a demo curve that does not reproduce its record's net return/drawdown."""

import pandas as pd
import pytest

from sq.research import equity_curves


def make_candles() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=3, freq="4h", tz="UTC")
    return pd.DataFrame({"date": dates, "close": [100.0, 100.0, 100.0]})


def make_trades() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=3, freq="4h", tz="UTC")
    return pd.DataFrame(
        [
            {
                "open_date": dates[0],
                "close_date": dates[-1],
                "open_rate": 100.0,
                "amount": 10.0,
                "fee_open": 0.0,
                "fee_close": 0.0,
                "profit_abs": 100.0,
                "profit_ratio": 0.1,
                "exit_reason": "roi",
            }
        ]
    )


def test_build_run_raises_when_net_return_disagrees_with_the_record(monkeypatch):
    monkeypatch.setattr(equity_curves, "load_backtest_data", lambda path: make_trades())

    # A flat 1,000 EUR wallet + a 100 EUR profit trade is a 10 % net return;
    # the record below claims 15 %, which does not match the rebuilt curve.
    record = {
        "result": "/freqtrade/user_data/backtest_results/H1/train-base-BTC_EUR/whatever.zip",
        "start": "2025-01-01",
        "end": "2025-01-02",
        "notional": 1000.0,
        "net_return_pct": 15.0,
        "mtm_max_drawdown_pct": 0.0,
    }
    benchmark = {
        "buy_hold_net_return_pct": 0.0,
        "buy_hold_cagr_pct": 0.0,
        "buy_hold_max_drawdown_pct": 0.0,
    }

    with pytest.raises(SystemExit):
        equity_curves.build_run(record, benchmark, make_candles(), fee=0.005)


def test_build_run_accepts_a_matching_record(monkeypatch):
    monkeypatch.setattr(equity_curves, "load_backtest_data", lambda path: make_trades())

    record = {
        "result": "/freqtrade/user_data/backtest_results/H1/train-base-BTC_EUR/whatever.zip",
        "start": "2025-01-01",
        "end": "2025-01-02",
        "notional": 1000.0,
        "net_return_pct": 10.0,
        "cagr_pct": 999.9,
        "mtm_max_drawdown_pct": 0.0,
    }
    benchmark = {
        "buy_hold_net_return_pct": 0.0,
        "buy_hold_cagr_pct": 0.0,
        "buy_hold_max_drawdown_pct": 0.0,
    }

    run = equity_curves.build_run(record, benchmark, make_candles(), fee=0.005)

    assert run["net_return_pct"] == 10.0
    assert run["trades"][0]["return_pct"] == 10.0
    assert run["buy_hold_cagr_pct"] == 0.0
