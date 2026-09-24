"""Validate the Binance-as-Kraken-proxy assumption (ADR-0002).

Fetches the latest 720 Kraken candles (public API, no credentials) for
BTC/EUR and ETH/EUR at 4h and 1d, aligns them with the local Binance feather
data on exact candle timestamps, and reports the overlap size, the Pearson
correlation of candle returns, and the median/p95 absolute close deviation in
basis points. Writes research/experiments/H1/proxy-check.json.

Gate set before the H1 runs: if any pair/timeframe shows return correlation < 0.95
or median deviation > 50 bps, this script still writes its result (so the
failure is recorded) but exits non-zero so callers can stop instead of
continuing to costed evaluation.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import ccxt
import pandas as pd

PAIRS = ["BTC/EUR", "ETH/EUR"]
TIMEFRAMES = ["4h", "1d"]
KRAKEN_LIMIT = 720
CORRELATION_MIN = 0.95
MEDIAN_DEVIATION_MAX_BPS = 50.0

DEFAULT_DATADIR = Path("/freqtrade/user_data/data/binance")
DEFAULT_OUT = Path("/freqtrade/research/experiments/H1/proxy-check.json")


def fetch_kraken_ohlcv(pair: str, timeframe: str, limit: int) -> pd.DataFrame:
    exchange = ccxt.kraken({"enableRateLimit": True})
    rows = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("date")


def load_binance_ohlcv(datadir: Path, pair: str, timeframe: str) -> pd.DataFrame:
    base, quote = pair.split("/")
    path = datadir / f"{base}_{quote}-{timeframe}.feather"
    df = pd.read_feather(path)
    return df.set_index("date")


def compare(kraken: pd.DataFrame, binance: pd.DataFrame) -> dict:
    aligned = kraken[["close"]].join(
        binance[["close"]], how="inner", lsuffix="_kraken", rsuffix="_binance"
    )
    n_overlap = int(len(aligned))
    if n_overlap < 3:
        return {
            "n_overlap": n_overlap,
            "return_correlation": None,
            "median_abs_deviation_bps": None,
            "p95_abs_deviation_bps": None,
            "note": "Fewer than 3 overlapping candles; correlation and deviation not computed.",
        }

    kraken_returns = aligned["close_kraken"].pct_change().dropna()
    binance_returns = aligned["close_binance"].pct_change().dropna()
    correlation = float(kraken_returns.corr(binance_returns))

    deviation_bps = (
        (aligned["close_kraken"] - aligned["close_binance"]).abs() / aligned["close_binance"]
    ) * 10_000
    return {
        "n_overlap": n_overlap,
        "return_correlation": correlation,
        "median_abs_deviation_bps": float(deviation_bps.median()),
        "p95_abs_deviation_bps": float(deviation_bps.quantile(0.95)),
        "kraken_first": aligned.index.min().isoformat(),
        "kraken_last": aligned.index.max().isoformat(),
    }


def passes(result: dict) -> bool:
    if result.get("return_correlation") is None:
        return False
    return (
        result["return_correlation"] >= CORRELATION_MIN
        and result["median_abs_deviation_bps"] <= MEDIAN_DEVIATION_MAX_BPS
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datadir", type=Path, default=DEFAULT_DATADIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    results: dict[str, dict[str, dict]] = {}
    all_pass = True
    for pair in PAIRS:
        results[pair] = {}
        for timeframe in TIMEFRAMES:
            kraken = fetch_kraken_ohlcv(pair, timeframe, KRAKEN_LIMIT)
            binance = load_binance_ohlcv(args.datadir, pair, timeframe)
            result = compare(kraken, binance)
            result["ok"] = passes(result)
            all_pass = all_pass and result["ok"]
            results[pair][timeframe] = result

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "correlation_min": CORRELATION_MIN,
        "median_deviation_max_bps": MEDIAN_DEVIATION_MAX_BPS,
        "kraken_candle_limit": KRAKEN_LIMIT,
        "results": results,
        "all_pass": all_pass,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out}. all_pass={all_pass}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
