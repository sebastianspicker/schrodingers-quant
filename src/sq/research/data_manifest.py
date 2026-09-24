"""Write research/data-manifest.json: provenance for the Binance proxy data.

Records exchange, pair, timeframe, first/last candle timestamp, row count and
the SHA-256 of each feather file in the data directory, so a rerun can prove
it used the same data (see docs/adr/0002-research-data-proxy.md).
"""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from sq.research.provenance import sha256_file

DEFAULT_DATADIR = Path("/freqtrade/user_data/data/binance")
DEFAULT_OUT = Path("/freqtrade/research/data-manifest.json")

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datadir",
        type=Path,
        default=DEFAULT_DATADIR,
        help="Directory of feather OHLCV files (default: user_data/data/binance).",
    )
    parser.add_argument("--exchange", default="binance")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output path (default: research/data-manifest.json).",
    )
    args = parser.parse_args()

    manifest = build_manifest(args.datadir, args.exchange)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out} ({len(manifest['entries'])} files).")


if __name__ == "__main__":
    main()
