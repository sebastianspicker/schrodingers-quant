"""Pure metric functions shared by the research pipeline: mark-to-market
equity/drawdown, the buy-and-hold benchmark, and the backtest summary metrics
Freqtrade's own `strategy_stats` need reshaping to report (exposure, sizing
against a fixed notional). No I/O here; callers (pipeline.py, jev_evaluation.py)
own reading trades/candles and writing the result.
"""

import pandas as pd

# --- mark-to-market equity ------------------------------------------------


def equity_curve(trades: pd.DataFrame, candles: pd.DataFrame, wallet: float) -> pd.Series:
    """Equity on each candle close; one open position at a time, fees included.

    Freqtrade's own account drawdown uses closed-trade balances only, so it
    hides adverse moves inside open trades. The buy-and-hold benchmark is
    marked to market on candle closes; this puts the strategy on the same
    basis so H1's K2 comparison is like for like.
    """
    closes = candles.set_index("date")["close"]
    equity = pd.Series(index=closes.index, dtype=float)
    balance = wallet
    cursor = closes.index[0]
    for trade in trades.sort_values("open_date").itertuples():
        equity[(closes.index >= cursor) & (closes.index < trade.open_date)] = balance
        held = (closes.index >= trade.open_date) & (closes.index < trade.close_date)
        open_value = trade.amount * trade.open_rate * (1 + trade.fee_open)
        exit_value = trade.amount * closes[held] * (1 - trade.fee_close)
        equity[held] = balance + exit_value - open_value
        balance += trade.profit_abs
        cursor = trade.close_date
    equity[closes.index >= cursor] = balance
    return equity


def max_drawdown_pct(series: pd.Series) -> float:
    """Peak-to-trough percentage drawdown of an equity or price series."""
    return float(((series.cummax() - series) / series.cummax()).max() * 100)


def mtm_report(
    *,
    result: str,
    pair: str,
    start: str,
    end: str,
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    wallet: float,
) -> dict:
    """The marked-to-market report for one backtest result: net return, CAGR
    and drawdown against a fixed notional (H1's fixed-stake sizing rule).

    `candles` must already be windowed to [start, end); `result` is recorded
    as given (the caller's choice of identifier, e.g. an absolute zip path).
    """
    equity = equity_curve(trades, candles, wallet)
    years = (candles["date"].iloc[-1] - candles["date"].iloc[0]).days / 365.25
    growth = float(equity.iloc[-1]) / wallet
    return {
        "result": result,
        "pair": pair,
        "start": start,
        "end": end,
        "trade_count": len(trades),
        "notional": wallet,
        "final_equity": round(float(equity.iloc[-1]), 2),
        "net_return_pct": round((growth - 1) * 100, 4),
        "cagr_pct": round((growth ** (1 / years) - 1) * 100, 4) if growth > 0 else None,
        "mtm_max_drawdown_pct": round(max_drawdown_pct(equity), 4),
    }


# --- buy-and-hold benchmark ------------------------------------------------


def buy_and_hold(
    *,
    pair: str,
    timeframe: str,
    start: str,
    end: str,
    candles: pd.DataFrame,
    fee: float,
) -> dict:
    """Buy-and-hold return and drawdown of a pair's close price over a
    period, with the same per-side fee as the strategy backtests (applied
    once on entry, once on exit). Independent of the Freqtrade backtester so
    it is not affected by strategy startup-candle offsets.

    `candles` must already be windowed to [start, end] and sorted by date.
    """
    start_close = float(candles["close"].iloc[0])
    end_close = float(candles["close"].iloc[-1])
    gross_return = end_close / start_close - 1
    net_multiplier = (1 - fee) * (1 - fee)
    net_return = (1 + gross_return) * net_multiplier - 1

    start_date = candles["date"].iloc[0]
    end_date = candles["date"].iloc[-1]
    days = max((end_date - start_date).total_seconds() / 86400, 1e-9)
    cagr = (1 + net_return) ** (365.25 / days) - 1 if net_return > -1 else float("-inf")

    return {
        "pair": pair,
        "timeframe": timeframe,
        "requested_start": start,
        "requested_end": end,
        "actual_start": start_date.isoformat(),
        "actual_end": end_date.isoformat(),
        "candle_count": int(len(candles)),
        "fee": fee,
        "start_close": start_close,
        "end_close": end_close,
        "buy_hold_gross_return_pct": round(gross_return * 100, 4),
        "buy_hold_net_return_pct": round(net_return * 100, 4),
        "buy_hold_cagr_pct": round(cagr * 100, 4),
        "buy_hold_max_drawdown_pct": round(max_drawdown_pct(candles["close"]), 4),
    }


# --- backtest summary metrics ----------------------------------------------


def exposure_pct(strategy_stats: dict) -> float:
    total_minutes = sum(trade["trade_duration"] for trade in strategy_stats["trades"])
    window_minutes = strategy_stats["backtest_days"] * 24 * 60
    if window_minutes <= 0:
        return 0.0
    return 100 * total_minutes / window_minutes


def backtest_metrics(strategy_stats: dict) -> dict:
    """Reshape one strategy's `freqtrade backtesting` stats into H1's
    reported metrics: net return, CAGR, max drawdown (account),
    return/max-drawdown, trade count, win rate, average holding time and
    exposure."""
    profit_total = strategy_stats["profit_total"]
    max_dd = strategy_stats["max_drawdown_account"]
    return {
        "net_total_return_pct": round(profit_total * 100, 4),
        "cagr_pct": round(strategy_stats["cagr"] * 100, 4),
        "max_drawdown_account_pct": round(max_dd * 100, 4),
        "return_over_max_drawdown": (round(profit_total / max_dd, 4) if max_dd else None),
        "trade_count": strategy_stats["total_trades"],
        "win_rate_pct": round(strategy_stats["winrate"] * 100, 4),
        "avg_holding_time_hours": round(strategy_stats["holding_avg_s"] / 3600, 2),
        "exposure_pct": round(exposure_pct(strategy_stats), 4),
        "market_change_pct": round(strategy_stats["market_change"] * 100, 4),
    }
