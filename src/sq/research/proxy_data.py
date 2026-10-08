"""The tracked proxy candle history for workstation research (no Docker).

`research/data/binance-BTC_EUR-4h.csv.gz` holds the same Binance BTC/EUR 4h
history the research container downloads (ADR-0002: Binance is the proxy for
Kraken, validated by `proxy_check`), as a compressed CSV so that the
physics-informed null models and their drift test can run anywhere.
`research/data/manifest.json` records its provenance and hash; the record's
pinned `research/data-manifest.json` is left untouched because the H1 result
files carry its SHA-256.

The file is validated on load with the same rules as the forward candle
archive, except that one known gap (Binance maintenance, 2020-02-19) is
tolerated because Freqtrade's backtest ran over the same rows.
"""

import gzip
import hashlib
from pathlib import Path

import pandas as pd

from sq.live.candles import COLUMNS, CandleError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = REPO_ROOT / "research" / "data" / "binance-BTC_EUR-4h.csv.gz"
MANIFEST_PATH = REPO_ROOT / "research" / "data" / "manifest.json"
STEP = pd.Timedelta("4h")
KNOWN_GAPS = {pd.Timestamp("2020-02-19T16:00:00Z")}


def sha256(path: Path = DEFAULT_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_candles(path: Path = DEFAULT_PATH) -> pd.DataFrame:
    with gzip.open(path, "rt") as handle:
        candles = pd.read_csv(handle)
    if not set(COLUMNS).issubset(candles.columns):
        raise CandleError(f"{path.name}: expected columns {COLUMNS}")
    candles["date"] = pd.to_datetime(candles["date"], utc=True, format="%Y-%m-%dT%H:%M:%SZ")
    candles = candles[COLUMNS].reset_index(drop=True)
    dates = candles["date"]
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise CandleError(f"{path.name}: timestamps must be unique and increasing")
    if (dates != dates.dt.floor(STEP)).any():
        raise CandleError(f"{path.name}: timestamps are not aligned to 4h")
    prices = candles[["open", "high", "low", "close"]]
    if not (prices > 0).all().all() or not prices.notna().all().all():
        raise CandleError(f"{path.name}: prices must be finite and positive")
    if (candles["high"] < prices.max(axis=1)).any() or (candles["low"] > prices.min(axis=1)).any():
        raise CandleError(f"{path.name}: invalid OHLC bounds")
    gaps = dates[1:][(dates.diff().iloc[1:] != STEP).to_numpy()]
    unexpected = set(gaps) - KNOWN_GAPS
    if unexpected:
        raise CandleError(f"{path.name}: unexpected gaps at {sorted(unexpected)[:3]}")
    return candles
