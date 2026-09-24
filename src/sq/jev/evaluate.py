"""D2 harness: offline matched comparison of a baseline against a Jev filter.

Given a Freqtrade backtest trades export (or a list of trade dicts) and a
Jev `assessments.jsonl` file, computes two trade sets on the *same* trades:
  - baseline: every trade, unchanged.
  - filtered: only trades whose entry candidate had an "approve" assessment
    that was available strictly before the trade's entry time (no leakage;
    a late or missing assessment excludes the trade, it does not include it).

Freqtrade's own trade records carry no candidate_id (confirm_trade_entry is
called before the Trade exists, so it cannot attach one to it). This module
therefore matches each trade to the nearest *preceding* recorded candidate
for the same pair, within `--max-lag-hours` of the trade's entry time, using
the candidate payload embedded in each assessment's `input` field. This is an
approximation of the true candidate-to-trade link, documented here and in
README.md; treat unmatched or ambiguous trades conservatively (they count as
"lost for lack of a timely assessment", never as an assumed approval).

Net return is measured against a fixed notional (`--wallet`), consistent with
H1's fixed-stake sizing (ADR-0005) and sq.research.mtm_drawdown. If
`--candles` is given, marked-to-market drawdown is also reported for both
trade sets, reusing `sq.research.mtm_drawdown.equity_curve`.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from sq.jev.protocol import parse_utc, read_jsonl
from sq.research.mtm_drawdown import equity_curve, max_drawdown_pct

DEFAULT_MAX_LAG_HOURS = 8.0


# --- loading ---------------------------------------------------------


def load_trades(source: Path | str | list[dict]) -> pd.DataFrame:
    """Load a Freqtrade backtest trades export (.zip/dir/file) or a list of
    trade dicts into a DataFrame with UTC `open_date`/`close_date`."""
    if isinstance(source, list):
        trades = pd.DataFrame(source)
    else:
        # Imported lazily: only needed for the file-export path, so unit
        # tests that pass a plain list of dicts never need the Freqtrade
        # backtest result format.
        from freqtrade.data.btanalysis import load_backtest_data

        trades = load_backtest_data(Path(source))

    trades = trades.copy()
    trades["open_date"] = pd.to_datetime(trades["open_date"], utc=True)
    trades["close_date"] = pd.to_datetime(trades["close_date"], utc=True)
    return trades


def load_assessments(path: Path | str) -> list[dict]:
    return list(read_jsonl(Path(path), strict=True))


# --- matching ---------------------------------------------------------


def _candidate_pool_from_assessments(assessments: list[dict]) -> dict[str, list[dict]]:
    """One entry per candidate_id (the newest by available_at if duplicated),
    grouped by pair and sorted by signal_candle_time."""
    best: dict[str, dict] = {}
    for record in assessments:
        candidate_id = record.get("candidate_id")
        if not candidate_id:
            continue
        input_payload = record.get("input") or {}
        pair = input_payload.get("pair")
        signal_candle_time = parse_utc(input_payload.get("signal_candle_time"))
        if pair is None or signal_candle_time is None:
            continue
        available_at = parse_utc(record.get("available_at"))
        entry = {
            "candidate_id": candidate_id,
            "pair": pair,
            "signal_candle_time": signal_candle_time,
            "decision": record.get("decision"),
            "available_at": available_at,
        }
        existing = best.get(candidate_id)
        if existing is None or (
            available_at is not None
            and (existing["available_at"] is None or available_at >= existing["available_at"])
        ):
            best[candidate_id] = entry

    by_pair: dict[str, list[dict]] = {}
    for entry in best.values():
        by_pair.setdefault(entry["pair"], []).append(entry)
    for pair_entries in by_pair.values():
        pair_entries.sort(key=lambda e: e["signal_candle_time"])
    return by_pair


def match_trades(
    trades: pd.DataFrame, assessments: list[dict], max_lag_hours: float = DEFAULT_MAX_LAG_HOURS
) -> pd.DataFrame:
    """One row per trade (trades sorted by open_date), with a match `reason`
    and `included` flag for the filtered comparison.

    reason:
      - "approved": a candidate matched, its newest assessment was available
        before entry and decided "approve" -> included in the filtered set.
      - "rejected": matched, available before entry, decision != "approve".
      - "late_or_missing_availability": matched, but the assessment became
        available at or after the trade's entry time (would be leakage to
        use it), or has no usable `available_at`.
      - "no_assessment": no recorded, assessed candidate within
        `max_lag_hours` of the trade's entry for that pair.
    Each candidate is matched to at most one trade (candidates strictly older
    than the trade being matched are never reconsidered for a later trade).
    """
    pool = _candidate_pool_from_assessments(assessments)
    cursors = {pair: 0 for pair in pool}
    sorted_trades = trades.sort_values("open_date").reset_index(drop=True)

    rows = []
    for trade in sorted_trades.itertuples():
        pair = trade.pair
        candidates = pool.get(pair, [])
        idx = cursors.get(pair, 0)
        best_idx = None
        while idx < len(candidates) and candidates[idx]["signal_candle_time"] <= trade.open_date:
            best_idx = idx
            idx += 1
        cursors[pair] = idx

        if best_idx is None:
            rows.append(
                {
                    "open_date": trade.open_date,
                    "pair": pair,
                    "candidate_id": None,
                    "reason": "no_assessment",
                    "included": False,
                }
            )
            continue

        match = candidates[best_idx]
        lag_hours = (trade.open_date - match["signal_candle_time"]).total_seconds() / 3600
        if lag_hours > max_lag_hours:
            rows.append(
                {
                    "open_date": trade.open_date,
                    "pair": pair,
                    "candidate_id": None,
                    "reason": "no_assessment",
                    "included": False,
                }
            )
        elif match["available_at"] is None or match["available_at"] >= trade.open_date:
            rows.append(
                {
                    "open_date": trade.open_date,
                    "pair": pair,
                    "candidate_id": match["candidate_id"],
                    "reason": "late_or_missing_availability",
                    "included": False,
                }
            )
        elif match["decision"] != "approve":
            rows.append(
                {
                    "open_date": trade.open_date,
                    "pair": pair,
                    "candidate_id": match["candidate_id"],
                    "reason": "rejected",
                    "included": False,
                }
            )
        else:
            rows.append(
                {
                    "open_date": trade.open_date,
                    "pair": pair,
                    "candidate_id": match["candidate_id"],
                    "reason": "approved",
                    "included": True,
                }
            )

    return pd.DataFrame(rows)


# --- summary -----------------------------------------------------------


def _trade_set_summary(trades: pd.DataFrame, wallet: float, candles: pd.DataFrame | None) -> dict:
    trade_count = len(trades)
    if trade_count == 0:
        summary = {
            "trade_count": 0,
            "net_return_pct": 0.0,
            "final_equity": wallet,
        }
    else:
        final_equity = wallet + float(trades["profit_abs"].sum())
        summary = {
            "trade_count": trade_count,
            "net_return_pct": round((final_equity / wallet - 1) * 100, 4),
            "final_equity": round(final_equity, 2),
        }

    if candles is not None and trade_count > 0:
        equity = equity_curve(trades, candles, wallet)
        summary["mtm_max_drawdown_pct"] = round(max_drawdown_pct(equity), 4)
    elif candles is not None:
        summary["mtm_max_drawdown_pct"] = 0.0

    return summary


def evaluate(
    trades: pd.DataFrame,
    assessments: list[dict],
    *,
    wallet: float,
    candles: pd.DataFrame | None = None,
    max_lag_hours: float = DEFAULT_MAX_LAG_HOURS,
) -> dict:
    """Baseline vs. filtered comparison report; see the module docstring."""
    sorted_trades = trades.sort_values("open_date").reset_index(drop=True)
    matches = match_trades(sorted_trades, assessments, max_lag_hours=max_lag_hours)

    filtered_trades = sorted_trades[matches["included"].to_numpy()]
    reason_counts = matches["reason"].value_counts().to_dict()
    skipped = int((~matches["included"]).sum())
    lost_for_lack_of_assessment = int(
        reason_counts.get("no_assessment", 0) + reason_counts.get("late_or_missing_availability", 0)
    )

    return {
        "baseline": _trade_set_summary(sorted_trades, wallet, candles),
        "filtered": _trade_set_summary(filtered_trades, wallet, candles),
        "skipped_trades": skipped,
        "trades_lost_for_lack_of_timely_assessment": lost_for_lack_of_assessment,
        "rejected_trades": int(reason_counts.get("rejected", 0)),
        "reason_counts": {str(k): int(v) for k, v in reason_counts.items()},
    }


# --- CLI -----------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trades", required=True, help="Backtest trades export (.zip or directory)"
    )
    parser.add_argument("--assessments", required=True, help="Path to assessments.jsonl")
    parser.add_argument(
        "--wallet", type=float, required=True, help="Fixed notional, e.g. the H1 stake"
    )
    parser.add_argument("--max-lag-hours", type=float, default=DEFAULT_MAX_LAG_HOURS)
    parser.add_argument("--candles", help="Optional OHLCV file/datadir for mark-to-market drawdown")
    parser.add_argument("--pair", default="BTC/EUR")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--out")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    trades = load_trades(args.trades)
    assessments = load_assessments(args.assessments)

    candles = None
    if args.candles:
        from freqtrade.data.history import load_pair_history

        candles = load_pair_history(
            pair=args.pair, timeframe=args.timeframe, datadir=Path(args.candles)
        )

    report = evaluate(
        trades, assessments, wallet=args.wallet, candles=candles, max_lag_hours=args.max_lag_hours
    )
    report["generated_at"] = datetime.now(UTC).isoformat()
    text = json.dumps(report, indent=2, sort_keys=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
