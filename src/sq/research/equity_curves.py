"""Daily equity curves for the recorded H1 runs, for the GitHub Pages demo.

Reads the recorded `mtm-*.json` summaries of an experiment, reloads each
backtest result they name, and rebuilds the marked-to-market equity with
`metrics.equity_curve`. Buy-and-hold is marked on the same closes with the
same fee on entry and exit; its headline figures are the recorded
`benchmark-*.json` values (base fee, the comparator H1's decision rule uses).
Both curves are sampled at the last 4h close of each UTC
day. The script refuses to write if a rebuilt curve does not reproduce the
recorded net return and drawdown, so the demo cannot drift from the record.

Only equity values (in EUR on the fixed notional), trade dates and trade
returns are written; no prices.
"""

import json
from pathlib import Path

import pandas as pd
from freqtrade.data.btanalysis import load_backtest_data
from freqtrade.data.history import load_pair_history

from sq.research import h1
from sq.research.metrics import equity_curve, max_drawdown_pct

# The demo shows H1's BTC/EUR runs, keyed "<period>-<cost>" (e.g. "train-base").
RUNS = h1.TRAIN_RUNS + h1.VALIDATION_RUNS + h1.HELDOUT_RUNS
COST_LABELS = {h1.FEE_BASE: "base", h1.FEE_STRESS: "stress"}


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
        "buy_hold_cagr_pct": benchmark["buy_hold_cagr_pct"],
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


def build_equity_curves(experiment: Path, datadir: Path, out: Path) -> None:
    candles = load_pair_history(pair=h1.PAIR, timeframe=h1.TIMEFRAME, datadir=datadir)
    out_pair = h1.PAIR.replace("/", "_")
    runs: dict[str, dict] = {}
    for run in RUNS:
        benchmark_file = experiment / f"benchmark-{run.period}-{out_pair}.json"
        benchmark = json.loads(benchmark_file.read_text())
        record = json.loads((experiment / f"mtm-{run.name}.json").read_text())
        key = f"{run.period}-{COST_LABELS[run.fee]}"
        runs[key] = build_run(record, benchmark, candles, run.fee)
    payload = {
        "pair": h1.PAIR,
        "timeframe": h1.TIMEFRAME,
        "notional_eur": int(h1.NOTIONAL),
        "source": "research/experiments/H1 (mtm-*.json and the backtest results they name)",
        "runs": runs,
    }
    out.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"wrote {out}")
