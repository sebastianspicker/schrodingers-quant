"""Write research/data-manifest.json: provenance for the Binance proxy data.

Records exchange, pair, timeframe, first/last candle timestamp, row count and
the SHA-256 of each feather file in the data directory, so a rerun can prove
it used the same data (see docs/adr/0002-research-data-proxy.md).
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from sq.research.provenance import sha256_file


def feather_path(datadir: Path, pair: str, timeframe: str) -> Path:
    """The feather file Freqtrade stores `pair`'s `timeframe` candles in."""
    base, quote = pair.split("/")
    return datadir / f"{base}_{quote}-{timeframe}.feather"


# The inverse of feather_path, for describing whatever files the datadir holds.
FEATHER_NAME_RE = re.compile(
    r"^(?P<base>[A-Z0-9]+)_(?P<quote>[A-Z0-9]+)-(?P<timeframe>\w+)\.feather$"
)


def describe_feather(path: Path, exchange: str) -> dict:
    match = FEATHER_NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"Unrecognized OHLCV filename: {path.name}")
    pair = f"{match['base']}/{match['quote']}"
    df = pd.read_feather(path)
    first = df["date"].min()
    last = df["date"].max()
    return {
        "exchange": exchange,
        "pair": pair,
        "timeframe": match["timeframe"],
        "row_count": int(len(df)),
        "first_candle": first.isoformat(),
        "last_candle": last.isoformat(),
        "sha256": sha256_file(path),
        "file": path.name,
    }


def build_manifest(datadir: Path, exchange: str) -> dict:
    entries = sorted(
        (describe_feather(p, exchange) for p in sorted(datadir.glob("*.feather"))),
        key=lambda e: (e["pair"], e["timeframe"]),
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "datadir": str(datadir),
        "entries": entries,
    }


def write_manifest(datadir: Path, out: Path, exchange: str = "binance") -> dict:
    manifest = build_manifest(datadir, exchange)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out} ({len(manifest['entries'])} files).")
    return manifest
