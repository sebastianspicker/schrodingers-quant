"""Mark-to-market max drawdown of a single-position Freqtrade backtest.

Freqtrade's account drawdown uses closed-trade balances only, so it hides
adverse moves inside open trades. The buy-and-hold benchmark is marked to
market on candle closes; this script puts the strategy on the same basis so
H1's K2 comparison is like for like.

With a fixed stake (H1's sizing rule), pass the stake as --wallet: equity is then
the fixed notional plus cumulative profit, and return, CAGR and drawdown are
measured against that notional rather than an oversized backtest wallet.
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from freqtrade.data.btanalysis import load_backtest_data
from freqtrade.data.history import load_pair_history


def equity_curve(trades: pd.DataFrame, candles: pd.DataFrame, wallet: float) -> pd.Series:
    """Equity on each candle close; one open position at a time, fees included."""
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


def max_drawdown_pct(equity: pd.Series) -> float:
    return float(((equity.cummax() - equity) / equity.cummax()).max() * 100)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, help="Backtest result .zip or directory")
    parser.add_argument("--datadir", default="/freqtrade/user_data/data/binance")
    parser.add_argument("--pair", default="BTC/EUR")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD, exclusive")
    parser.add_argument("--wallet", type=float, default=1000.0)
    parser.add_argument("--out")
    args = parser.parse_args()

    trades = load_backtest_data(Path(args.result))
    candles = load_pair_history(
        pair=args.pair, timeframe=args.timeframe, datadir=Path(args.datadir)
    )
    start, end = pd.Timestamp(args.start, tz="UTC"), pd.Timestamp(args.end, tz="UTC")
    candles = candles[(candles["date"] >= start) & (candles["date"] < end)]
    equity = equity_curve(trades, candles, args.wallet)
    years = (candles["date"].iloc[-1] - candles["date"].iloc[0]).days / 365.25
    growth = float(equity.iloc[-1]) / args.wallet
    report = {
        "result": args.result,
        "pair": args.pair,
        "start": args.start,
        "end": args.end,
        "trade_count": len(trades),
        "notional": args.wallet,
        "final_equity": round(float(equity.iloc[-1]), 2),
        "net_return_pct": round((growth - 1) * 100, 4),
        "cagr_pct": round((growth ** (1 / years) - 1) * 100, 4) if growth > 0 else None,
        "mtm_max_drawdown_pct": round(max_drawdown_pct(equity), 4),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
