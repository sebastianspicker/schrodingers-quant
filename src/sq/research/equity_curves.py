"""Daily equity curves for the recorded H1 runs, for the GitHub Pages demo.

Reads the recorded `mtm-*.json` summaries of an experiment, reloads each
backtest result they name, and rebuilds the marked-to-market equity with
`mtm_drawdown.equity_curve`. Buy-and-hold is marked on the same closes with the
same fee on entry and exit; its headline figures are the recorded
`benchmark-*.json` values (base fee, the comparator H1's decision rule uses).
Both curves are sampled at the last 4h close of each UTC
day. The script refuses to write if a rebuilt curve does not reproduce the
recorded net return and drawdown, so the demo cannot drift from the record.

Only equity values (in EUR on the fixed notional), trade dates and trade
returns are written; no prices.
"""

import argparse
import json
from pathlib import Path

import pandas as pd
from freqtrade.data.btanalysis import load_backtest_data
from freqtrade.data.history import load_pair_history

from sq.research.mtm_drawdown import equity_curve, max_drawdown_pct

RUNS = {
    "train": "mtm-train-{cost}-BTC_EUR.json",
    "validation": "mtm-validation-{cost}-BTC_EUR.json",
    "heldout": "mtm-heldout-{cost}-BTC_EUR.json",
}
FEES = {"base": 0.005, "stress": 0.01}
EXPERIMENT = Path("/freqtrade/research/experiments/H1")
DATADIR = Path("/freqtrade/user_data/data/binance")


def daily(series: pd.Series) -> list[float]:
    return [round(float(v), 2) for v in series.resample("1D").last().dropna()]


def build_run(record: dict, benchmark: dict, candles: pd.DataFrame, fee: float) -> dict:
    trades = load_backtest_data(Path(record["result"]))
    start = pd.Timestamp(record["start"], tz="UTC")
    end = pd.Timestamp(record["end"], tz="UTC")
    window = candles[(candles["date"] >= start) & (candles["date"] < end)]
    notional = record["notional"]

    equity = equity_curve(trades, window, notional)
    net = (float(equity.iloc[-1]) / notional - 1) * 100
    drawdown = max_drawdown_pct(equity)
    if (
        abs(net - record["net_return_pct"]) > 0.01
        or abs(drawdown - record["mtm_max_drawdown_pct"]) > 0.01
    ):
        raise SystemExit(
            f"{record['result']}: rebuilt curve ({net:.4f} %, DD {drawdown:.4f} %) does not match "
            f"the record ({record['net_return_pct']} %, DD {record['mtm_max_drawdown_pct']} %)"
        )

    closes = window.set_index("date")["close"]
    hold = notional * (1 - fee) ** 2 * closes / closes.iloc[0]
    days = equity.resample("1D").last().dropna().index
    return {
        "start": record["start"],
        "end": record["end"],
        "dates": [d.strftime("%Y-%m-%d") for d in days],
        "strategy": daily(equity),
        "buy_hold": daily(hold),
        "net_return_pct": record["net_return_pct"],
        "cagr_pct": record["cagr_pct"],
        "mtm_max_drawdown_pct": record["mtm_max_drawdown_pct"],
        "buy_hold_net_return_pct": benchmark["buy_hold_net_return_pct"],
        "buy_hold_max_drawdown_pct": benchmark["buy_hold_max_drawdown_pct"],
        "trades": [
            {
                "open": t.open_date.strftime("%Y-%m-%d %H:%M"),
                "close": t.close_date.strftime("%Y-%m-%d %H:%M"),
                "return_pct": round(float(t.profit_ratio) * 100, 2),
                "exit": t.exit_reason,
            }
            for t in trades.sort_values("open_date").itertuples()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=EXPERIMENT)
    parser.add_argument("--datadir", type=Path, default=DATADIR)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    candles = load_pair_history(pair="BTC/EUR", timeframe="4h", datadir=args.datadir)
    runs: dict[str, dict] = {}
    for period, pattern in RUNS.items():
        benchmark = json.loads((args.experiment / f"benchmark-{period}-BTC_EUR.json").read_text())
        for cost, fee in FEES.items():
            record = json.loads((args.experiment / pattern.format(cost=cost)).read_text())
            runs[f"{period}-{cost}"] = build_run(record, benchmark, candles, fee)
    payload = {
        "pair": "BTC/EUR",
        "timeframe": "4h",
        "notional_eur": 1000,
        "source": "research/experiments/H1 (mtm-*.json and the backtest results they name)",
        "runs": runs,
    }
    args.out.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
