"""Buy-and-hold benchmark: return and max drawdown of a pair's close price
over a period, with the same per-side fee as the strategy backtests (applied
once on entry, once on exit). This is the "buy-and-hold" comparator H1
reports alongside the strategy's results, computed independently of the
Freqtrade backtester so it is not affected by strategy startup-candle offsets.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

DEFAULT_DATADIR = Path("/freqtrade/user_data/data/binance")


def load_close_series(
    datadir: Path, pair: str, timeframe: str, start: str, end: str
) -> pd.DataFrame:
    base, quote = pair.split("/")
    path = datadir / f"{base}_{quote}-{timeframe}.feather"
    df = pd.read_feather(path)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    windowed = df[(df["date"] >= start_ts) & (df["date"] <= end_ts)]
    if windowed.empty:
        raise ValueError(f"No candles for {pair} {timeframe} in [{start}, {end}]")
    return windowed.sort_values("date").reset_index(drop=True)


def max_drawdown_pct(close: pd.Series) -> float:
    running_peak = close.cummax()
    drawdown = (running_peak - close) / running_peak
    return float(drawdown.max() * 100)


def benchmark(datadir: Path, pair: str, timeframe: str, start: str, end: str, fee: float) -> dict:
    df = load_close_series(datadir, pair, timeframe, start, end)
    start_close = float(df["close"].iloc[0])
    end_close = float(df["close"].iloc[-1])
    gross_return = end_close / start_close - 1
    net_multiplier = (1 - fee) * (1 - fee)
    net_return = (1 + gross_return) * net_multiplier - 1

    start_date = df["date"].iloc[0]
    end_date = df["date"].iloc[-1]
    days = max((end_date - start_date).total_seconds() / 86400, 1e-9)
    cagr = (1 + net_return) ** (365.25 / days) - 1 if net_return > -1 else float("-inf")

    return {
        "pair": pair,
        "timeframe": timeframe,
        "requested_start": start,
        "requested_end": end,
        "actual_start": start_date.isoformat(),
        "actual_end": end_date.isoformat(),
        "candle_count": int(len(df)),
        "fee": fee,
        "start_close": start_close,
        "end_close": end_close,
        "buy_hold_gross_return_pct": round(gross_return * 100, 4),
        "buy_hold_net_return_pct": round(net_return * 100, 4),
        "buy_hold_cagr_pct": round(cagr * 100, 4),
        "buy_hold_max_drawdown_pct": round(max_drawdown_pct(df["close"]), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--fee", type=float, required=True)
    parser.add_argument("--datadir", type=Path, default=DEFAULT_DATADIR)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    result = benchmark(args.datadir, args.pair, args.timeframe, args.start, args.end, args.fee)
    result["generated_at"] = datetime.now(UTC).isoformat()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out}")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
