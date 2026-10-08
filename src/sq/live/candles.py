"""Validated closed candles and an incremental, transactional SQLite archive.

One daily public-data request is sufficient at 4h. No service, credentials or
new dependencies. Existing closed candles are immutable: a provider revision
requires investigation rather than silently rewriting forward evidence.
"""

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class CandleError(ValueError):
    """Safe, actionable messages containing no config or account information."""


def validate(candles: pd.DataFrame, step: pd.Timedelta) -> dict:
    if candles.empty or not set(COLUMNS).issubset(candles.columns):
        raise CandleError("candles missing; supply a complete OHLCV history")
    dates = candles["date"]
    if dates.isna().any() or dates.dt.tz is None:
        raise CandleError("candle timestamps must be valid UTC timestamps")
    if not dates.is_monotonic_increasing or dates.duplicated().any():
        raise CandleError("candle timestamps must be unique and increasing")
    if (dates != dates.dt.floor(step)).any():
        raise CandleError("candle timestamps are not aligned to the timeframe")
    values = candles[COLUMNS[1:]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any():
        raise CandleError("candle prices must be finite and positive")
    if (values[:, 4] < 0).any():
        raise CandleError("candle volume must be non-negative")
    if (candles["high"] < candles[["open", "close", "low"]].max(axis=1)).any() or (
        candles["low"] > candles[["open", "close", "high"]].min(axis=1)
    ).any():
        raise CandleError("invalid OHLC price bounds")
    gaps = dates.diff().iloc[1:] != step
    if gaps.any():
        raise CandleError("candle history has gaps; import missing candles before reporting")
    digest = hashlib.sha256(candles[COLUMNS].to_csv(index=False).encode()).hexdigest()
    return {
        "status": "COMPLETE",
        "candles": len(candles),
        "first": dates.iloc[0].isoformat(),
        "last": dates.iloc[-1].isoformat(),
        "sha256": digest,
    }


def require_fresh(candles: pd.DataFrame, step: pd.Timedelta, now: pd.Timestamp) -> None:
    # Allow one interval for a slow exchange publication; never accept a stale
    # archive as today's evidence after an outage.
    if candles.empty or now - (candles["date"].iloc[-1] + step) > step:
        raise CandleError("latest closed candle is stale; check the market-data feed")


def archive_update(
    path: Path, incoming: pd.DataFrame, pair: str, timeframe: str, step: pd.Timedelta
) -> pd.DataFrame:
    """Merge closed candles atomically, preserving history outside API retention.

    A transaction serializes writers and rolls back gaps or revisions. The
    database is separate from Freqtrade and keyed by exchange/pair/timeframe.
    """
    validate(incoming, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candles (exchange TEXT, pair TEXT, timeframe TEXT, "
            "stamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, "
            "PRIMARY KEY(exchange, pair, timeframe, stamp))"
        )
        key = ("kraken", pair, timeframe)
        rows = connection.execute(
            "SELECT stamp, open, high, low, close, volume FROM candles "
            "WHERE exchange=? AND pair=? AND timeframe=? ORDER BY stamp",
            key,
        ).fetchall()
        old = pd.DataFrame(rows, columns=COLUMNS)
        old["date"] = pd.to_datetime(old["date"], unit="ms", utc=True)
        if not old.empty:
            overlap = old.merge(incoming, on="date", suffixes=("_old", "_new"))
            for column in COLUMNS[1:]:
                if not np.allclose(
                    overlap[column + "_old"], overlap[column + "_new"], rtol=1e-10, atol=1e-12
                ):
                    raise CandleError(
                        "archived closed candles changed; investigate provider revision"
                    )
            merged = pd.concat([old, incoming]).drop_duplicates("date").sort_values("date")
        else:
            merged = incoming.copy()
        merged = merged.reset_index(drop=True)
        validate(merged, step)
        connection.executemany(
            "INSERT OR IGNORE INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    *key,
                    int(row.date.timestamp() * 1000),
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.volume,
                )
                for row in incoming.itertuples()
            ],
        )
        connection.commit()
        return merged
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
