"""Summarize a completed `freqtrade backtesting` run into H1's reported metrics.

Reads the latest result in --results-dir (a `--backtest-directory` used for a
single run), extracts net total return, CAGR, max drawdown (account),
return/max-drawdown, trade count, win rate, average holding time and exposure
for the named strategy, and writes them with full provenance (timerange, fee,
pair, strategy file hash, image digest, data manifest hash) to --out.
"""

import argparse
import json
import zipfile
from pathlib import Path

from sq.research.provenance import image_ref, sha256_file

DEFAULT_MANIFEST = Path("/freqtrade/research/data-manifest.json")


def load_latest_result(results_dir: Path) -> dict:
    pointer = json.loads((results_dir / ".last_result.json").read_text())
    zip_path = results_dir / pointer["latest_backtest"]
    with zipfile.ZipFile(zip_path) as zf:
        json_name = zip_path.stem + ".json"
        payload = json.loads(zf.read(json_name))
    return payload


def exposure_pct(strategy_stats: dict) -> float:
    total_minutes = sum(trade["trade_duration"] for trade in strategy_stats["trades"])
    window_minutes = strategy_stats["backtest_days"] * 24 * 60
    if window_minutes <= 0:
        return 0.0
    return 100 * total_minutes / window_minutes


def build_summary(
    strategy_stats: dict,
    *,
    period: str,
    fee: float,
    strategy_file: Path,
    manifest_path: Path | None,
) -> dict:
    profit_total = strategy_stats["profit_total"]
    max_dd = strategy_stats["max_drawdown_account"]
    metrics = {
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
    manifest_hash = sha256_file(manifest_path) if manifest_path and manifest_path.exists() else None
    provenance = {
        "period": period,
        "fee": fee,
        "timerange": strategy_stats["timerange"],
        "backtest_start": strategy_stats["backtest_start"],
        "backtest_end": strategy_stats["backtest_end"],
        "pairlist": strategy_stats["pairlist"],
        "strategy_name": strategy_stats["strategy_name"],
        "strategy_file": str(strategy_file),
        "strategy_file_sha256": sha256_file(strategy_file),
        "image_digest": image_ref(),
        "data_manifest_sha256": manifest_hash,
        "enable_protections": strategy_stats["enable_protections"],
        "stoploss": strategy_stats["stoploss"],
        "minimal_roi": strategy_stats["minimal_roi"],
    }
    return {"metrics": metrics, "provenance": provenance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--period", required=True, help="train, validation, sensitivity, ...")
    parser.add_argument("--fee", type=float, required=True)
    parser.add_argument("--strategy-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = load_latest_result(args.results_dir)
    strategy_stats = payload["strategy"][args.strategy]
    summary = build_summary(
        strategy_stats,
        period=args.period,
        fee=args.fee,
        strategy_file=args.strategy_file,
        manifest_path=args.manifest,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {args.out}")
    print(json.dumps(summary["metrics"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
