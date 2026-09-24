"""Mark-to-market equity used for H1's K2 drawdown comparison."""

import pandas as pd
import pytest

from sq.research.mtm_drawdown import equity_curve, max_drawdown_pct


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
