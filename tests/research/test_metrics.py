"""Tests for sq.research.metrics: mark-to-market equity/drawdown (ported from
the former tests/research/test_mtm_drawdown.py), the buy-and-hold benchmark,
and the backtest summary reshaping (backtest_metrics/exposure_pct)."""

import pandas as pd
import pytest

from sq.research.metrics import backtest_metrics, buy_and_hold, equity_curve, max_drawdown_pct


def candles(closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({"date": dates, "close": closes})


def trade(open_i: int, close_i: int, rate: float, amount: float, profit: float) -> dict:
    dates = pd.date_range("2025-01-01", periods=20, freq="4h", tz="UTC")
    return {
        "open_date": dates[open_i],
        "close_date": dates[close_i],
        "open_rate": rate,
        "amount": amount,
        "fee_open": 0.0,
        "fee_close": 0.0,
        "profit_abs": profit,
    }


# --- equity_curve / max_drawdown_pct --------------------------------------


def test_open_trade_giveback_counts_as_drawdown():
    # Price doubles inside the trade, then falls back to +50 % before the exit.
    data = candles([100, 100, 150, 200, 150, 150, 150])
    trades = pd.DataFrame([trade(1, 4, 100.0, 10.0, 500.0)])

    equity = equity_curve(trades, data, 1000.0)

    assert equity.max() == pytest.approx(2000.0)
    assert equity.iloc[-1] == pytest.approx(1500.0)
    assert max_drawdown_pct(equity) == pytest.approx(25.0)


def test_flat_between_trades_and_fees_reduce_equity():
    data = candles([100, 100, 100, 100])
    row = trade(1, 2, 100.0, 10.0, -20.0)
    row.update(fee_open=0.01, fee_close=0.01)
    equity = equity_curve(pd.DataFrame([row]), data, 1000.0)

    assert equity.iloc[0] == pytest.approx(1000.0)
    # While open: value after exit fee (990) minus cost incl. entry fee (1010).
    assert equity.iloc[1] == pytest.approx(980.0)
    assert equity.iloc[-1] == pytest.approx(980.0)


# --- buy_and_hold -----------------------------------------------------


def test_buy_and_hold_return_and_drawdown_hand_computed():
    # Close 100 -> 150 -> 90 every 4h, 1 % fee each side.
    data = candles([100.0, 150.0, 90.0])

    result = buy_and_hold(
        pair="BTC/EUR", timeframe="4h", start="2025-01-01", end="2025-01-02", candles=data, fee=0.01
    )

    # gross_return = 90/100 - 1 = -0.10; net = (1 - 0.10) * 0.99 * 0.99 - 1.
    assert result["buy_hold_gross_return_pct"] == pytest.approx(-10.0)
    assert result["buy_hold_net_return_pct"] == pytest.approx(-11.791)
    # Peak 150 at candle 2, trough 90 at candle 3: (150 - 90) / 150 = 40 %.
    assert result["buy_hold_max_drawdown_pct"] == pytest.approx(40.0)
    assert result["candle_count"] == 3
    assert result["start_close"] == pytest.approx(100.0)
    assert result["end_close"] == pytest.approx(90.0)
    assert result["pair"] == "BTC/EUR"
    assert result["fee"] == 0.01


# --- backtest_metrics / exposure_pct ---------------------------------------


def make_strategy_stats(**overrides) -> dict:
    stats = {
        "profit_total": 0.25,
        "cagr": 0.10,
        "max_drawdown_account": 0.05,
        "total_trades": 4,
        "winrate": 0.75,
        "holding_avg_s": 7200,
        "market_change": 0.5,
        "backtest_days": 10,
        "trades": [
            {"trade_duration": 60},
            {"trade_duration": 120},
            {"trade_duration": 180},
            {"trade_duration": 240},
        ],
    }
    stats.update(overrides)
    return stats


def test_backtest_metrics_hand_computed():
    metrics = backtest_metrics(make_strategy_stats())

    assert metrics == {
        "net_total_return_pct": 25.0,
        "cagr_pct": 10.0,
        "max_drawdown_account_pct": 5.0,
        "return_over_max_drawdown": 5.0,
        "trade_count": 4,
        "win_rate_pct": 75.0,
        "avg_holding_time_hours": 2.0,
        "exposure_pct": 4.1667,
        "market_change_pct": 50.0,
    }


def test_backtest_metrics_zero_drawdown_has_no_return_over_drawdown():
    metrics = backtest_metrics(make_strategy_stats(max_drawdown_account=0.0))

    assert metrics["return_over_max_drawdown"] is None
