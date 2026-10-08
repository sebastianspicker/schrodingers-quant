"""Forward paper-trading report: judge the dry-run (or the live pilot) with the
same metrics as the backtest and against predeclared criteria.

The backtest record (research/experiments/H1) says what H1 did on simulated
fills. The forward test is the only evidence that comes from data the strategy
could not have been chosen on, and it is worth nothing unless it is measured
the same way and judged by rules fixed in advance (docs/forward-test.md). This
module produces that measurement from two read-only sources:

- Freqtrade's trade database (`trades` and `orders` tables), opened with
  `mode=ro`; every trade the bot opened or closed, with its fills and fees.
- Public candles for the pair, fetched from Kraken's public OHLC endpoint via
  ccxt (no credentials; the latest 720 candles, incrementally archived locally) or loaded
  from a file passed with `--candles` for longer windows.

It reports, per forward window:

1. **Performance, marked to market** on the configured stake, with
   `sq.research.metrics.equity_curve` (the function behind the backtest's
   decision metrics), so the forward return and drawdown are on the same basis
   as the record. Open positions are marked at the last candle close.
2. **Execution quality** per trade: the fill price against the backtest's
   fill assumption (the open of the candle after the signal candle), the fill
   delay after the candle close, the fees actually charged, and the realized
   round-trip cost in percent, compared with the 0.5 % and 1.0 % per side the
   hypothesis assumed. Dry-run fills are Freqtrade's simulation and will look
   near-perfect; the live pilot is where this table matters.
3. **Signal fidelity**: the strategy's own `populate_*` methods are run on the
   candles and every entry the bot made is matched to an entry signal. An
   entry without a signal is an implementation defect. A signal without an
   entry is explained if the bot was in a position (or in the one-candle
   cooldown after an exit); otherwise it is reported for the operator to
   explain (protection lock, bot stopped, Jev block).
4. **Order handling**: counts of orders by side and status, so unfilled and
   cancelled entries are visible.
5. **The forward criteria F1–F4** (docs/forward-test.md) and a verdict:
   CONTINUE, STOP, or NOT_YET_TESTABLE for criteria that need data that does
   not exist yet.

This module never references an order-mutating ccxt method and never writes
to the trade database. Exit code: 0 CONTINUE, 4 STOP, 1 error.
"""

import argparse
import hashlib
import inspect
import json
import math
import os
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd

from sq.config import TRACKED_BASE_CONFIG, load_config
from sq.live import candles as market_data
from sq.research import h2, metrics
from sq.research.statistics import Trade, bootstrap_trades, exposure_pct, trade_statistics

# H1.md cost assumptions, per side.
ASSUMED_COST_PER_SIDE = 0.005
STRESS_COST_PER_SIDE = 0.01
# Trades needed before a performance judgement is attempted (docs/forward-test.md, F4).
MIN_TRADES_FOR_JUDGEMENT = 30
# H1's decision rule: the record's K2 limit for the held-out period. Used as the
# forward drawdown stop (F3) when the record is not at hand.
DEFAULT_MAX_DRAWDOWN_PCT = 31.31
KRAKEN_PUBLIC_CANDLE_LIMIT = 720

EXIT_CONTINUE = 0
EXIT_ERROR = 1
EXIT_STOP = 4


# --- trade database (read-only) --------------------------------------------


@dataclass(frozen=True)
class PaperTrade:
    """The fields of one `trades` row the report needs. Dates are UTC."""

    id: int
    pair: str
    is_open: bool
    open_date: pd.Timestamp
    close_date: pd.Timestamp | None
    open_rate: float
    close_rate: float | None
    amount: float
    stake_amount: float
    fee_open: float
    fee_close: float | None
    profit_abs: float | None
    profit_ratio: float | None
    exit_reason: str | None
    strategy: str | None
    timeframe: str | None
    submitted_date: pd.Timestamp | None = None
    exit_submitted_date: pd.Timestamp | None = None


class ReportInputError(ValueError):
    """A safe operator-facing contract error, without account data or secrets."""


def filled_snapshot(db_path: str, pairs: list[str]) -> tuple[list[PaperTrade], dict[str, int]]:
    """One consistent read transaction, using the pinned Freqtrade order schema.

    Unfilled entry intents are not positions. Multiple filled entries/exits,
    partial exits and outstanding partial fills require a fill-ledger engine;
    this single-entry H1 reporter explicitly refuses those instead of guessing.
    """
    connection = sqlite3.connect(read_only_uri(db_path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        placeholders = ",".join("?" for _ in pairs)
        trades = trades_from_rows(
            connection.execute(
                f"SELECT {TRADE_COLUMNS} FROM trades WHERE pair IN ({placeholders}) "
                "ORDER BY open_date",
                pairs,
            ).fetchall()
        )
        orders = connection.execute(
            "SELECT ft_trade_id, ft_order_side, status, ft_is_open, filled, average, "
            "price, order_date, order_filled_date, ft_fee_base FROM orders "
            f"WHERE ft_pair IN ({placeholders}) ORDER BY id",
            pairs,
        ).fetchall()
        precision = {
            r[0]: (r[1], r[2])
            for r in connection.execute(
                "SELECT id, amount_precision, precision_mode FROM trades "
                f"WHERE pair IN ({placeholders})",
                pairs,
            ).fetchall()
        }
    except sqlite3.OperationalError as exc:
        raise ReportInputError(
            "trade database missing or incompatible with the pinned schema"
        ) from exc
    finally:
        connection.close()
    counts: dict[str, int] = {}
    for order in orders:
        key = f"{order['ft_order_side']}:{order['status'] or 'unknown'}"
        counts[key] = counts.get(key, 0) + 1
    out = []
    for trade in trades:
        fills = [o for o in orders if o["ft_trade_id"] == trade.id and (o["filled"] or 0) > 0]
        if any(
            o["ft_is_open"]
            or not o["order_filled_date"]
            or o["status"] not in ("closed", "canceled", "cancelled", "expired", "rejected")
            for o in fills
        ):
            raise ReportInputError("incomplete or nonterminal filled order; reconcile orders")
        entries = [o for o in fills if o["ft_order_side"] == "buy"]
        exits = [o for o in fills if o["ft_order_side"] in ("sell", "stoploss")]
        if not entries and trade.is_open and not exits:
            continue  # An entry order exists but no risk has filled yet.
        if (
            len(entries) != 1
            or len(exits) != (0 if trade.is_open else 1)
            or len(fills) != len(entries) + len(exits)
        ):
            raise ReportInputError("multiple fills or partial exits unsupported; reconcile orders")
        entry = entries[0]
        expected_amount = float(entry["filled"]) - float(entry["ft_fee_base"] or 0)
        digits, mode = precision[trade.id]
        if digits is None or mode not in (2, 3, 4):
            raise ReportInputError("trade lacks exchange precision; reconcile quantities")
        if not math.isfinite(digits) or digits < 0 or (mode == 4 and digits == 0):
            raise ReportInputError("invalid exchange amount precision")
        if expected_amount <= 0:
            raise ReportInputError("filled entry has no positive net quantity")
        unit = (
            float(digits)
            if mode == 4
            else 10.0 ** -int(digits)
            if mode == 2
            else 10.0 ** (math.floor(math.log10(expected_amount)) - int(digits) + 1)
        )
        if not math.isclose(trade.amount, expected_amount, rel_tol=1e-10, abs_tol=unit * 1.000001):
            raise ReportInputError("trade quantity disagrees with filled entry; reconcile orders")
        if exits and not math.isclose(
            float(exits[0]["filled"]), trade.amount, rel_tol=1e-10, abs_tol=unit * 1.000001
        ):
            raise ReportInputError("exit quantity disagrees with position; reconcile orders")
        out.append(
            replace(
                trade,
                submitted_date=trade.open_date,
                open_date=parse_db_timestamp(entry["order_filled_date"]),
                exit_submitted_date=parse_db_timestamp(exits[0]["order_date"]) if exits else None,
                close_date=parse_db_timestamp(exits[0]["order_filled_date"]) if exits else None,
            )
        )
    return out, counts


def db_path_from_url(db_url: str) -> str:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix) or len(db_url) == len(prefix):
        raise ValueError(f"Only sqlite db_url values are supported, got: {db_url}")
    return db_url[len(prefix) :]


def parse_db_timestamp(value: str | None) -> pd.Timestamp | None:
    """Freqtrade stores naive UTC timestamps."""
    if not value:
        return None
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


TRADE_COLUMNS = (
    "id, pair, is_open, open_date, close_date, open_rate, close_rate, amount, stake_amount, "
    "fee_open, fee_close, close_profit_abs, close_profit, exit_reason, strategy, timeframe"
)


def trades_from_rows(rows: list[tuple]) -> list[PaperTrade]:
    return [
        PaperTrade(
            id=int(row[0]),
            pair=row[1],
            is_open=bool(row[2]),
            open_date=parse_db_timestamp(row[3]),
            close_date=parse_db_timestamp(row[4]),
            open_rate=float(row[5]),
            close_rate=float(row[6]) if row[6] is not None else None,
            amount=float(row[7]),
            stake_amount=float(row[8]),
            fee_open=float(row[9]) if row[9] is not None else 0.0,
            fee_close=float(row[10]) if row[10] is not None else None,
            profit_abs=float(row[11]) if row[11] is not None else None,
            profit_ratio=float(row[12]) if row[12] is not None else None,
            exit_reason=row[13],
            strategy=row[14],
            timeframe=row[15],
        )
        for row in rows
    ]


def read_only_uri(db_path: str) -> str:
    """sqlite URI that opens `db_path` read-only. The path is percent-encoded
    so that `?`, `#` or `%` in a file name cannot swallow `mode=ro`."""
    return f"file:{quote(db_path)}?mode=ro"


def read_paper_trades(db_path: str, pairs: list[str]) -> list[PaperTrade]:
    """All trades for `pairs`, oldest first, from a read-only connection."""
    connection = sqlite3.connect(read_only_uri(db_path), uri=True)
    try:
        placeholders = ",".join("?" for _ in pairs)
        rows = connection.execute(
            f"SELECT {TRADE_COLUMNS} FROM trades WHERE pair IN ({placeholders}) ORDER BY open_date",
            pairs,
        ).fetchall()
    finally:
        connection.close()
    return trades_from_rows(rows)


def read_order_counts(db_path: str, pairs: list[str]) -> dict[str, int]:
    """Orders by `<ft_order_side>:<status>` (Freqtrade sides are `buy`, `sell`,
    `stoploss`; e.g. `buy:closed`, `buy:canceled`)."""
    connection = sqlite3.connect(read_only_uri(db_path), uri=True)
    try:
        placeholders = ",".join("?" for _ in pairs)
        rows = connection.execute(
            f"""
            SELECT ft_order_side, COALESCE(status, 'unknown'), COUNT(*)
            FROM orders WHERE ft_pair IN ({placeholders})
            GROUP BY ft_order_side, status
            """,
            pairs,
        ).fetchall()
    finally:
        connection.close()
    return {f"{side}:{status}": int(count) for side, status, count in rows}


# --- candles -------------------------------------------------------------------


def timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    units = {"m": "min", "h": "h", "d": "D"}
    return pd.Timedelta(int(timeframe[:-1]), units[timeframe[-1]])


def candles_from_ohlcv(rows: list[list[float]]) -> pd.DataFrame:
    """ccxt OHLCV rows (ms timestamp, o, h, l, c, v) to the Freqtrade frame layout."""
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], unit="ms", utc=True)
    frame = frame.drop_duplicates()
    if frame["date"].duplicated().any():
        raise market_data.CandleError("conflicting duplicate candles")
    return frame.sort_values("date").reset_index(drop=True)


def fetch_kraken_candles(pair: str, timeframe: str) -> pd.DataFrame:
    """Public Kraken OHLC via ccxt: the latest 720 candles, no credentials."""
    import ccxt

    client = ccxt.kraken({"enableRateLimit": True, "timeout": 30000})
    rows = client.fetch_ohlcv(pair, timeframe, limit=KRAKEN_PUBLIC_CANDLE_LIMIT)
    if not rows:
        raise ValueError(f"Kraken returned no {timeframe} candles for {pair}")
    return candles_from_ohlcv(rows)


def closed_candles(candles: pd.DataFrame, step: pd.Timedelta, now: pd.Timestamp) -> pd.DataFrame:
    """Drop candles that have not closed yet (Kraken's public OHLC includes the
    forming one; Freqtrade itself only acts on closed candles)."""
    return candles[candles["date"] + step <= now].reset_index(drop=True)


def load_candles_file(path: Path) -> pd.DataFrame:
    """Candles from a file: Freqtrade feather, or JSON as ccxt OHLCV rows."""
    if path.suffix == ".feather":
        frame = pd.read_feather(path)
        frame["date"] = pd.to_datetime(frame["date"], utc=True)
        return frame.sort_values("date").reset_index(drop=True)
    return candles_from_ohlcv(json.loads(path.read_text()))


# --- execution quality ---------------------------------------------------------------


@dataclass(frozen=True)
class TradeExecution:
    trade_id: int
    open_date: str
    close_date: str | None
    entry_reference_open: float | None
    entry_slippage_bps: float | None
    entry_delay_minutes: float | None
    exit_reference_open: float | None
    exit_slippage_bps: float | None
    fee_open_bps: float
    fee_close_bps: float | None
    round_trip_cost_pct: float | None
    exit_reason: str | None
    profit_ratio_pct: float | None


def reference_open(candles: pd.DataFrame, at: pd.Timestamp, step: pd.Timedelta) -> float | None:
    """Open of the candle containing `at`: the backtest's fill price for a
    decision taken on the previous candle's close."""
    candle_start = at.floor(step)
    match = candles.loc[candles["date"] == candle_start, "open"]
    return float(match.iloc[0]) if len(match) else None


def execution_of(trade: PaperTrade, candles: pd.DataFrame, step: pd.Timedelta) -> TradeExecution:
    decision = trade.submitted_date if trade.submitted_date is not None else trade.open_date
    entry_ref = reference_open(candles, decision, step)
    entry_slip = (trade.open_rate / entry_ref - 1) * 10_000 if entry_ref else None
    delay = (trade.open_date - decision.floor(step)).total_seconds() / 60

    # Only a signal exit is decided on a candle close and filled at the next
    # open; a stop, ROI or forced exit is defined by price or by the operator,
    # so the next-open reference does not apply and F2 must not count it.
    exit_ref = exit_slip = None
    if trade.close_date is not None and trade.close_rate and trade.exit_reason == "exit_signal":
        exit_decision = (
            trade.exit_submitted_date if trade.exit_submitted_date is not None else trade.close_date
        )
        exit_ref = reference_open(candles, exit_decision, step)
        # Positive when the exit received less than the reference.
        exit_slip = (1 - trade.close_rate / exit_ref) * 10_000 if exit_ref else None

    fee_open_bps = trade.fee_open * 10_000
    fee_close_bps = trade.fee_close * 10_000 if trade.fee_close is not None else None
    round_trip = None
    if entry_slip is not None and exit_slip is not None and fee_close_bps is not None:
        round_trip = (entry_slip + exit_slip + fee_open_bps + fee_close_bps) / 100

    return TradeExecution(
        trade_id=trade.id,
        open_date=trade.open_date.isoformat(),
        close_date=trade.close_date.isoformat() if trade.close_date is not None else None,
        entry_reference_open=entry_ref,
        entry_slippage_bps=round(entry_slip, 2) if entry_slip is not None else None,
        entry_delay_minutes=round(delay, 1),
        exit_reference_open=exit_ref,
        exit_slippage_bps=round(exit_slip, 2) if exit_slip is not None else None,
        fee_open_bps=round(fee_open_bps, 2),
        fee_close_bps=round(fee_close_bps, 2) if fee_close_bps is not None else None,
        round_trip_cost_pct=round(round_trip, 4) if round_trip is not None else None,
        exit_reason=trade.exit_reason,
        profit_ratio_pct=round(trade.profit_ratio * 100, 4)
        if trade.profit_ratio is not None
        else None,
    )


def execution_report(trades: list[PaperTrade], candles: pd.DataFrame, step: pd.Timedelta) -> dict:
    executions = [execution_of(t, candles, step) for t in trades]
    costs = [e.round_trip_cost_pct for e in executions if e.round_trip_cost_pct is not None]
    entry_slips = [e.entry_slippage_bps for e in executions if e.entry_slippage_bps is not None]
    summary: dict[str, Any] = {
        "closed_round_trips_measured": len(costs),
        "assumed_round_trip_cost_pct": ASSUMED_COST_PER_SIDE * 2 * 100,
        "stress_round_trip_cost_pct": STRESS_COST_PER_SIDE * 2 * 100,
    }
    if costs:
        series = pd.Series(costs)
        summary.update(
            {
                "round_trip_cost_pct_mean": round(float(series.mean()), 4),
                "round_trip_cost_pct_median": round(float(series.median()), 4),
                "round_trip_cost_pct_max": round(float(series.max()), 4),
            }
        )
    if entry_slips:
        summary["entry_slippage_bps_mean"] = round(float(pd.Series(entry_slips).mean()), 2)
    return {"summary": summary, "trades": [asdict(e) for e in executions]}


# --- signal fidelity ---------------------------------------------------------------


def compute_signals(strategy: Any, candles: pd.DataFrame, pair: str) -> pd.DataFrame:
    """Run the strategy's own populate_* methods on the candles. Returns the
    frame with `enter_long` and `exit_long` columns (0/1)."""
    metadata = {"pair": pair}
    frame = strategy.populate_indicators(candles.copy(), metadata)
    frame = strategy.populate_entry_trend(frame, metadata)
    frame = strategy.populate_exit_trend(frame, metadata)
    for column in ("enter_long", "exit_long"):
        if column not in frame:
            frame[column] = 0
    return frame


def signal_times(frame: pd.DataFrame, column: str) -> list[pd.Timestamp]:
    """Open times of the candles whose close carries a signal in `column`."""
    return list(frame.loc[frame[column].fillna(0).astype(int) == 1, "date"])


def signal_fidelity(
    entry_signals: list[pd.Timestamp],
    trades: list[PaperTrade],
    step: pd.Timedelta,
    *,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    signals_valid_from: pd.Timestamp | None = None,
    acknowledged: list[pd.Timestamp] | None = None,
) -> dict:
    """Match bot entries to strategy entry signals.

    A signal on the candle opening at S is acted on after that candle closes
    at S + step; the backtest fills at the open of the next candle, the bot
    within that candle. An entry is matched if it opened in [S + step, S + 2 step].

    Signals computed on a candle history are undefined for the strategy's
    startup candles; `signals_valid_from` is the first candle with a defined
    signal. Entries before the candle after it cannot be verified and are
    listed separately rather than counted as defects.

    The signal candle just before the window start counts: the window starts
    at the first candle after `/start`, and the first entry may act on the
    candle that closed at that moment. `acknowledged` lists signal candles the
    operator has explained (a drill, a protection lock); they are reported as
    explained rather than as defects.
    """
    valid_from = signals_valid_from if signals_valid_from is not None else window_start - step
    acknowledged = set(acknowledged or [])
    signals = sorted(
        s for s in entry_signals if max(window_start - step, valid_from) <= s < window_end
    )
    matched: dict[int, pd.Timestamp] = {}
    unexpected: list[dict] = []
    unverifiable: list[dict] = []
    for trade in trades:
        if not (window_start <= trade.open_date < window_end):
            continue
        decision = trade.submitted_date if trade.submitted_date is not None else trade.open_date
        if decision < valid_from + step:
            unverifiable.append({"trade_id": trade.id, "open_date": trade.open_date.isoformat()})
            continue
        candidates = [s for s in signals if s + step <= decision < s + 2 * step]
        if candidates:
            matched[trade.id] = candidates[-1]
        else:
            unexpected.append({"trade_id": trade.id, "open_date": trade.open_date.isoformat()})

    acted = set(matched.values())
    explained: list[dict] = []
    unexplained: list[dict] = []
    for signal in signals:
        if signal in acted:
            continue
        acting_time = signal + step
        in_position = any(
            t.open_date <= acting_time
            and (t.close_date is None or t.close_date + step > acting_time)  # incl. cooldown
            for t in trades
        )
        entry = {"signal_candle": signal.isoformat(), "acting_time": acting_time.isoformat()}
        if signal in acknowledged:
            explained.append({**entry, "reason": "acknowledged by the operator"})
        elif in_position:
            explained.append({**entry, "reason": "in position or cooldown"})
        elif acting_time + step > window_end:
            explained.append({**entry, "reason": "entry window still open"})
        else:
            unexplained.append(entry)

    return {
        "signals_valid_from": valid_from.isoformat(),
        "window_coverage_complete": valid_from + step <= window_start,
        "entry_signals_in_window": len(signals),
        "entries_matched": len(matched),
        "entries_unverifiable": unverifiable,
        "entries_without_signal": unexpected,
        "signals_without_entry_explained": explained,
        "signals_without_entry_unexplained": unexplained,
    }


# --- performance ---------------------------------------------------------------


def trades_frame(
    trades: list[PaperTrade], mark_at: pd.Timestamp, mark_price: float, step: pd.Timedelta
) -> pd.DataFrame:
    """The columns `metrics.equity_curve` needs. An open trade is closed
    synthetically at `mark_at`/`mark_price` for the mark-to-market only.

    Fill times are floored to their candle: the backtest's trades carry candle
    open times, and `equity_curve` holds a position for candles with
    `open_date <= date < close_date`. Real fill times (a few seconds into the
    candle) would otherwise skip the entry candle and mark the exit candle as
    still held, which misstates the drawdown that decides F3."""
    rows = []
    for t in trades:
        open_date = t.open_date.floor(step)
        if t.is_open or t.close_date is None:
            exit_fee = t.fee_open  # best available assumption for the unpaid exit fee
            open_value = t.amount * t.open_rate * (1 + t.fee_open)
            profit_abs = t.amount * mark_price * (1 - exit_fee) - open_value
            rows.append(
                {
                    "open_date": open_date,
                    "close_date": mark_at,
                    "amount": t.amount,
                    "open_rate": t.open_rate,
                    "fee_open": t.fee_open,
                    "fee_close": exit_fee,
                    "profit_abs": profit_abs,
                }
            )
        else:
            rows.append(
                {
                    "open_date": open_date,
                    "close_date": t.close_date.floor(step),
                    "amount": t.amount,
                    "open_rate": t.open_rate,
                    "fee_open": t.fee_open,
                    "fee_close": t.fee_close if t.fee_close is not None else t.fee_open,
                    "profit_abs": t.profit_abs if t.profit_abs is not None else 0.0,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "open_date",
            "close_date",
            "amount",
            "open_rate",
            "fee_open",
            "fee_close",
            "profit_abs",
        ],
    )


def mark_frame(trades: list[PaperTrade], candles: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    """`trades_frame` with open positions marked at the last candle's close."""
    return trades_frame(
        trades, candles["date"].iloc[-1] + step, float(candles["close"].iloc[-1]), step
    )


def validate_trades(
    trades: list[PaperTrade], pair: str, timeframe: str, strategy: str | None
) -> None:
    previous_close = None
    for t in sorted(trades, key=lambda x: x.open_date):
        if t.pair != pair or str(t.timeframe) not in (
            timeframe,
            str(int(timeframe_to_timedelta(timeframe).total_seconds() / 60)),
        ):
            raise ReportInputError(
                "mixed pair or timeframe in trade database; use a separate window"
            )
        if strategy and t.strategy != strategy:
            raise ReportInputError("mixed strategies in trade database; use a separate window")
        if not all(math.isfinite(v) and v > 0 for v in (t.amount, t.open_rate, t.stake_amount)):
            raise ReportInputError("invalid filled trade quantity, rate or stake")
        if not 0 <= t.fee_open < 1 or (t.fee_close is not None and not 0 <= t.fee_close < 1):
            raise ReportInputError("invalid trade fees")
        if not t.is_open and (
            t.close_date is None or t.profit_abs is None or t.profit_ratio is None
        ):
            raise ReportInputError("closed trade has incomplete realized accounting")
        if (t.profit_abs is not None and not math.isfinite(t.profit_abs)) or (
            t.profit_ratio is not None and not math.isfinite(t.profit_ratio)
        ):
            raise ReportInputError("non-finite realized profit")
        if not t.is_open and (
            t.close_rate is None or not math.isfinite(t.close_rate) or t.close_rate <= 0
        ):
            raise ReportInputError("closed trade has invalid exit rate")
        if t.close_date is not None and t.close_date < t.open_date:
            raise ReportInputError("trade closes before its entry")
        if previous_close is not None and t.open_date < previous_close:
            raise ReportInputError(
                "overlapping positions unsupported by the single-position report"
            )
        previous_close = (
            t.close_date if t.close_date is not None else pd.Timestamp.max.tz_localize("UTC")
        )


def at_horizon(trades: list[PaperTrade], end: pd.Timestamp) -> list[PaperTrade]:
    """DB events beyond the last closed candle must not leak into this report."""
    return [
        replace(
            t,
            is_open=True,
            close_date=None,
            close_rate=None,
            fee_close=None,
            profit_abs=None,
            profit_ratio=None,
            exit_reason=None,
        )
        if t.close_date is not None and t.close_date >= end
        else t
        for t in trades
        if t.open_date < end
    ]


def normalized(trades: list[PaperTrade], notional: float) -> list[PaperTrade]:
    """Reference fixed stake; independent of account wallet and rounding/minimums."""
    return [
        replace(
            t,
            amount=t.amount * notional / t.stake_amount,
            profit_abs=t.profit_abs * notional / t.stake_amount
            if t.profit_abs is not None
            else None,
            stake_amount=notional,
        )
        for t in trades
    ]


def benchmark_report(candles: pd.DataFrame, notional: float) -> dict:
    # Fully funded spot buy-and-hold: entry fee is included in the initial cash
    # budget, and each mark reserves the assumed exit fee.
    equity = (
        notional
        / (1 + ASSUMED_COST_PER_SIDE)
        / float(candles["open"].iloc[0])
        * candles.set_index("date")["close"]
        * (1 - ASSUMED_COST_PER_SIDE)
    )
    return {
        "basis": "same 4h window; buy at first open; entry and liquidation fees 0.5% per side",
        "net_return_pct": round((float(equity.iloc[-1]) / notional - 1) * 100, 4),
        "mtm_max_drawdown_pct": round(metrics.max_drawdown_pct(equity, initial=notional), 4),
    }


def performance_decision(
    performance: dict, benchmark: dict, criteria: dict, trades: list[PaperTrade]
) -> dict:
    eligible = criteria["performance_judgement_allowed"]
    tests = [
        {
            "id": "P1",
            "description": "positive fixed-stake net return",
            "value_pct": performance["net_return_pct"],
            "threshold_pct": 0.0,
            "result": ("PASS" if performance["net_return_pct"] > 0 else "FAIL")
            if eligible
            else "NOT_YET_TESTABLE",
        },
        {
            "id": "P2",
            "description": "drawdown <= 0.6 times same-window buy-and-hold",
            "value_pct": performance["mtm_max_drawdown_pct"],
            "threshold_pct": round(0.6 * benchmark["mtm_max_drawdown_pct"], 4),
            "result": (
                "PASS"
                if performance["mtm_max_drawdown_pct"] <= 0.6 * benchmark["mtm_max_drawdown_pct"]
                else "FAIL"
            )
            if eligible
            else "NOT_YET_TESTABLE",
        },
    ]
    sample = [t.profit_ratio * 100 for t in trades if not t.is_open and t.profit_ratio is not None]
    uncertainty = bootstrap_trades(sample, resamples=2000)
    interval = uncertainty.get("mean_ci95_pct")
    return {
        "criteria": tests,
        "trade_uncertainty": uncertainty,
        "positive_mean_supported": bool(eligible and interval and interval[0] > 0),
        "capital_increase_allowed": False,
        "capital_note": "Requires live execution evidence within base costs and a reviewed ADR; "
        "paper fills cannot authorize capital.",
    }


def performance_report(
    trades: list[PaperTrade], candles: pd.DataFrame, notional: float, step: pd.Timedelta
) -> dict:
    """Marked-to-market performance on the fixed notional over the candle window."""
    if candles.empty:
        raise ValueError("no candles in the forward window")
    first = trades[0].open_date if trades else None
    if first is not None and first < candles["date"].iloc[0]:
        raise ValueError(
            "the candles start after the first trade; pass --candles with a longer history"
        )
    last_close_time = candles["date"].iloc[-1] + step
    frame = mark_frame(trades, candles, step)
    equity = metrics.equity_curve(frame, candles, notional)
    closed = [t for t in trades if not t.is_open and t.profit_ratio is not None]
    closed_stats = trade_statistics(
        [
            Trade(
                open=t.open_date,
                close=t.close_date,
                return_pct=t.profit_ratio * 100,
                exit=t.exit_reason or "unknown",
            )
            for t in closed
        ]
    )
    start, end = candles["date"].iloc[0], last_close_time
    forward_trades = [
        Trade(open=t.open_date, close=t.close_date or last_close_time, return_pct=0.0, exit="")
        for t in trades
    ]
    return {
        "notional": notional,
        "final_equity": round(float(equity.iloc[-1]), 4),
        "net_return_pct": round((float(equity.iloc[-1]) / notional - 1) * 100, 4),
        "mtm_max_drawdown_pct": round(metrics.max_drawdown_pct(equity, initial=notional), 4),
        "exposure_pct": round(exposure_pct(forward_trades, start, end), 4) if trades else 0.0,
        "open_trades": sum(1 for t in trades if t.is_open),
        "closed_trades": closed_stats,
        "equity_daily": [
            {"date": d.strftime("%Y-%m-%d"), "equity": round(float(v), 4)}
            for d, v in equity.resample("1D").last().dropna().items()
        ],
    }


# --- criteria -------------------------------------------------------------------


def evaluate_criteria(
    performance: dict,
    execution: dict,
    fidelity: dict,
    *,
    max_drawdown_pct: float,
    min_trades: int = MIN_TRADES_FOR_JUDGEMENT,
    trades_per_year_expected: float | None = None,
) -> dict:
    """The forward criteria F1–F4 of docs/forward-test.md."""
    closed = performance["closed_trades"]["count"]
    criteria = []

    # F1: implementation fidelity.
    unexpected = len(fidelity["entries_without_signal"])
    unexplained = len(fidelity["signals_without_entry_unexplained"])
    incomplete = not fidelity.get("window_coverage_complete", True) or bool(
        fidelity.get("entries_unverifiable")
    )
    if incomplete and not unexpected and not unexplained:
        f1 = "NOT_YET_TESTABLE"
    elif (
        fidelity["entry_signals_in_window"] == 0
        and fidelity["entries_matched"] == 0
        and not unexpected
    ):
        f1 = "NOT_YET_TESTABLE"
    else:
        f1 = "PASS" if unexpected == 0 and unexplained == 0 else "FAIL"
    criteria.append(
        {
            "id": "F1",
            "description": (
                "every entry has a strategy signal; every signal has an entry or an explanation"
            ),
            "entries_without_signal": unexpected,
            "signals_without_entry_unexplained": unexplained,
            "window_coverage_complete": fidelity.get("window_coverage_complete", True),
            "result": f1,
        }
    )

    # F2: realized round-trip cost within the stress assumption.
    summary = execution["summary"]
    measured = summary["closed_round_trips_measured"]
    if measured == 0:
        f2 = "NOT_YET_TESTABLE"
    else:
        f2 = (
            "PASS"
            if summary["round_trip_cost_pct_mean"] <= summary["stress_round_trip_cost_pct"]
            else "FAIL"
        )
    criteria.append(
        {
            "id": "F2",
            "description": "mean realized round-trip cost <= the stress assumption (2 x 1.0 %)",
            "value_pct": summary.get("round_trip_cost_pct_mean"),
            "threshold_pct": summary["stress_round_trip_cost_pct"],
            "within_base_assumption": (
                summary["round_trip_cost_pct_mean"] <= summary["assumed_round_trip_cost_pct"]
                if measured
                else None
            ),
            "result": f2,
        }
    )

    # F3: marked-to-market drawdown stop.
    dd = performance["mtm_max_drawdown_pct"]
    criteria.append(
        {
            "id": "F3",
            "description": "forward marked-to-market drawdown on the fixed stake <= the K2 limit",
            "value_pct": dd,
            "threshold_pct": max_drawdown_pct,
            "result": "PASS" if dd <= max_drawdown_pct else "FAIL",
        }
    )

    # F4: sample size before any performance judgement.
    remaining = max(0, min_trades - closed)
    years_remaining = (
        round(remaining / trades_per_year_expected, 1)
        if trades_per_year_expected and trades_per_year_expected > 0
        else None
    )
    criteria.append(
        {
            "id": "F4",
            "description": f"at least {min_trades} closed trades before judging performance",
            "closed_trades": closed,
            "threshold": min_trades,
            "trades_remaining": remaining,
            "years_remaining_at_expected_rate": years_remaining,
            "result": "PASS" if closed >= min_trades else "NOT_YET_TESTABLE",
        }
    )

    by_id = {c["id"]: c["result"] for c in criteria}
    if by_id["F3"] == "FAIL" or by_id["F1"] == "FAIL" or by_id["F2"] == "FAIL":
        verdict = "STOP"
    else:
        verdict = "CONTINUE"
    return {
        "criteria": criteria,
        "verdict": verdict,
        "performance_judgement_allowed": by_id["F4"] == "PASS" and by_id["F1"] == "PASS",
    }


# --- assembly ---------------------------------------------------------------------


def build_report(
    *,
    trades: list[PaperTrade],
    candles: pd.DataFrame,
    entry_signals: list[pd.Timestamp],
    order_counts: dict[str, int],
    notional: float,
    timeframe: str,
    pair: str,
    source: dict,
    since: pd.Timestamp | None,
    max_drawdown_pct: float,
    trades_per_year_expected: float | None,
    signals_valid_from: pd.Timestamp | None = None,
    acknowledged: list[pd.Timestamp] | None = None,
    initial_capital: float | None = None,
) -> dict:
    step = timeframe_to_timedelta(timeframe)
    quality = market_data.validate(candles, step)
    if not math.isfinite(notional) or notional <= 0:
        raise ReportInputError("reference stake must be finite and positive")
    validate_max_drawdown(max_drawdown_pct)
    validate_trades(trades, pair, timeframe, source.get("strategy"))
    end = candles["date"].iloc[-1] + step
    deferred = sum(t.open_date >= end for t in trades)
    trades = at_horizon(trades, end)
    if since is not None and since < candles["date"].iloc[0]:
        raise ReportInputError("window start predates candle history; import the complete history")
    if since is not None and since != since.floor(step):
        raise ReportInputError("window start must align with the timeframe")
    # The window starts at --since (the recorded start of the forward test).
    # Without it, it starts at the bot's first trade: signals from before the
    # bot existed must not count as missed entries. With neither, there is no
    # fidelity window at all and F1 stays NOT_YET_TESTABLE.
    window_since = since
    if window_since is None and trades:
        window_since = min(
            t.submitted_date if t.submitted_date is not None else t.open_date for t in trades
        ).floor(step)
    window = (
        candles
        if window_since is None
        else candles[candles["date"] >= window_since].reset_index(drop=True)
    )
    if window.empty:
        raise ValueError("no candles at or after the window start")
    window_start = window["date"].iloc[0]
    window_end = window["date"].iloc[-1] + step
    straddling = [
        t
        for t in trades
        if t.open_date < window_start and (t.close_date is None or t.close_date > window_start)
    ]
    if straddling:
        raise ValueError(
            "a position was open at the window start; pass --candles with a longer history "
            "or an earlier --since"
        )
    # Trades before an explicit --since are excluded on purpose (a new window).
    # Trades before the candle history's start, with no --since to justify it,
    # would silently turn the report into a rolling window: an earlier drawdown
    # breach or fidelity failure could age out. Refuse instead.
    dropped_by_history = [
        t for t in trades if t.open_date < window_start and (since is None or t.open_date >= since)
    ]
    if dropped_by_history:
        raise ValueError(
            f"{len(dropped_by_history)} trade(s) predate the candle history; pass --candles "
            "with a longer history, or --since to start a new window deliberately"
        )
    in_window = [t for t in trades if t.open_date >= window_start]

    reference_trades = normalized(in_window, notional)
    performance = performance_report(reference_trades, window, notional, step)
    performance["basis"] = (
        "each filled trade normalized to the fixed reference stake; not account return"
    )
    execution = execution_report(in_window, candles, step)
    fidelity = signal_fidelity(
        entry_signals if (since is not None or trades) else [],
        in_window,
        step,
        window_start=window_start,
        window_end=window_end,
        signals_valid_from=signals_valid_from,
        acknowledged=acknowledged,
    )
    fidelity["window_start_source"] = (
        "--since" if since is not None else ("first trade" if trades else "none (no trades yet)")
    )
    criteria = evaluate_criteria(
        performance,
        execution,
        fidelity,
        max_drawdown_pct=max_drawdown_pct,
        trades_per_year_expected=trades_per_year_expected,
    )
    # H2 (research/hypotheses/H2.md): the same trades re-sized by volatility,
    # reported alongside; judged only once F4 is met (criteria section says so).
    benchmark = benchmark_report(window, notional)
    decision = performance_decision(performance, benchmark, criteria, in_window)
    criteria["criteria"].extend(decision["criteria"])
    if any(c["result"] == "FAIL" for c in decision["criteria"]):
        criteria["verdict"] = "STOP"
    h2_frame = mark_frame(normalized(in_window, 1000.0), window, step)
    h2_frame["decision_date"] = [
        t.submitted_date if t.submitted_date is not None else t.open_date for t in in_window
    ]
    h2_paired = h2.paired_evaluation(
        h2_frame,
        window,
        1000.0,
        step,
        sizing_candles=candles,
        closed_trades=performance["closed_trades"]["count"],
        stopped=any(c["id"] == "F3" and c["result"] == "FAIL" for c in criteria["criteria"]),
        evidence_valid=all(
            c["result"] == "PASS" for c in criteria["criteria"] if c["id"] in ("F1", "F2")
        ),
    )
    if window_start < pd.Timestamp("2026-10-08", tz="UTC"):
        h2_paired.update(
            judgement_allowed=False,
            verdict="INCONCLUSIVE",
            note="window predates H2 declaration; descriptive only",
        )
    account = None
    if initial_capital is not None:
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ReportInputError("initial account capital must be finite and positive")
        account = performance_report(in_window, window, initial_capital, step)
        account["basis"] = "actual filled quantities; no deposits or withdrawals within the window"
    return {
        "protocol_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "pair": pair,
        "timeframe": timeframe,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": round((window_end - window_start).total_seconds() / 86400, 2),
            "candles": int(len(window)),
        },
        "performance": performance,
        "account": account,
        "benchmark": benchmark,
        "decision": decision,
        "data_quality": {**quality, "entries_after_mark_excluded": deferred},
        "execution": execution,
        "fidelity": fidelity,
        "orders": order_counts,
        "criteria": criteria,
        "h2_paired": h2_paired,
    }


def validate_max_drawdown(value: float) -> float:
    """The F3 stop must be a percentage strictly inside (0, 100): 0 or a
    non-finite value would disable the stop silently."""
    if not (0 < value < 100):
        raise ValueError("--max-drawdown-pct must be a percentage between 0 and 100 (exclusive)")
    return float(value)


def validate_out_path(path: Path) -> Path:
    """The report may only be written as a `.json` file inside a directory
    named `reports` (the writable runtime mount's reports folder), so a typo
    cannot overwrite a trade database or anything else."""
    resolved = path.resolve()
    if resolved.suffix != ".json" or resolved.parent.name != "reports":
        raise ValueError("--out must be a .json file inside a 'reports' directory")
    return resolved


def atomic_report(path: Path, report: dict) -> None:
    """Readers see either the old complete report or the new complete report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".forward-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_utc_arg(value: str) -> pd.Timestamp:
    """ISO 8601; naive values are UTC."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def record_k2_limit(repo_root: Path) -> float | None:
    """The K2 threshold from the tracked statistics record, if present."""
    path = repo_root / "research" / "experiments" / "H1" / "statistics.json"
    if not path.exists():
        return None
    decision = json.loads(path.read_text())["decision"]
    return next(c["threshold"] for c in decision["criteria"] if c["id"] == "K2")


def record_trades_per_year(repo_root: Path) -> float | None:
    path = repo_root / "research" / "experiments" / "H1" / "statistics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["runs"]["heldout-base"]["power"].get(
        "trades_per_year_observed"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        metavar="PATH",
        help="Layered config file list, later overrides earlier (default: the tracked base config)",
    )
    parser.add_argument(
        "--candles",
        type=Path,
        help="Candle file (Freqtrade .feather or ccxt OHLCV .json) instead of Kraken's public API",
    )
    parser.add_argument(
        "--since",
        help="ISO 8601 UTC start of the forward window (default: first filled trade)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="SQLite candle archive (default: market-data.sqlite beside trade DB)",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        help="Account capital at window start; required for live account returns",
    )
    parser.add_argument(
        "--max-drawdown-pct",
        type=float,
        help=f"F3 stop (default: the record's K2 limit, else {DEFAULT_MAX_DRAWDOWN_PCT})",
    )
    parser.add_argument(
        "--acknowledge",
        action="append",
        metavar="CANDLE",
        help=(
            "Open time (ISO 8601 UTC) of an entry-signal candle whose missing entry the operator "
            "has explained (drill, protection lock); may be repeated"
        ),
    )
    parser.add_argument("--out", type=Path, help="Write the JSON report here as well as to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    from freqtrade.enums import RunMode

    from sq.config import load_strategy

    config_paths = args.configs or [TRACKED_BASE_CONFIG]
    config = load_config(config_paths, RunMode.UTIL_NO_EXCHANGE)
    pairs = config["exchange"]["pair_whitelist"]
    if (
        len(pairs) != 1
        or config["exchange"]["name"] != "kraken"
        or config["timeframe"] != "4h"
        or config["strategy"] not in ("H1ChannelBreakout", "H1JevShadow")
    ):
        raise ReportInputError("forward protocol supports one Kraken 4h H1 pair per database")
    pair = pairs[0]
    timeframe = config["timeframe"]
    notional = float(config["stake_amount"])
    db_path = db_path_from_url(config["db_url"])
    repo_root = TRACKED_BASE_CONFIG.parents[1]

    trades, order_counts = filled_snapshot(db_path, [pair])
    candles = (
        load_candles_file(args.candles) if args.candles else fetch_kraken_candles(pair, timeframe)
    )
    step = timeframe_to_timedelta(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    candles = closed_candles(candles, step, now)
    market_data.validate(candles, step)
    market_data.require_fresh(candles, step, now)
    archive = args.archive or Path(db_path).parent / "market-data.sqlite"
    if archive.resolve() == Path(db_path).resolve():
        raise ReportInputError("candle archive must be separate from the trade database")
    if args.candles is None or args.archive is not None:
        candles = market_data.archive_update(archive, candles, pair, timeframe, step)

    strategy = load_strategy(config)
    # Freqtrade's resolver does not retain the dynamic module in sys.modules,
    # so inspect.getfile(class) is unreliable. Python methods retain filenames.
    strategy_files = {
        Path(inspect.getfile(method))
        for cls in type(strategy).__mro__
        if cls.__name__ in ("H1ChannelBreakout", "H1JevShadow")
        for method in cls.__dict__.values()
        if inspect.isfunction(method)
    }
    strategy_sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in strategy_files}
    strategy_hash = hashlib.sha256(
        json.dumps(strategy_sources, sort_keys=True).encode()
    ).hexdigest()
    measured_config = {
        key: config.get(key)
        for key in (
            "strategy",
            "timeframe",
            "stake_amount",
            "dry_run",
            "dry_run_wallet",
            "fee",
            "max_open_trades",
            "stoploss",
            "trailing_stop",
            "minimal_roi",
            "order_types",
            "unfilledtimeout",
            "entry_pricing",
            "exit_pricing",
            "use_exit_signal",
            "exit_profit_only",
            "ignore_roi_if_entry_signal",
            "protections",
            "jev",
        )
    }
    config_hash = hashlib.sha256(json.dumps(measured_config, sort_keys=True).encode()).hexdigest()
    signals = compute_signals(strategy, candles, pair)
    entry_signals = signal_times(signals, "enter_long")

    since = parse_utc_arg(args.since) if args.since else None
    startup = int(getattr(strategy, "startup_candle_count", 0))
    signals_valid_from = (
        candles["date"].iloc[min(startup, len(candles) - 1)] if len(candles) else None
    )
    if args.max_drawdown_pct is not None:
        max_dd = validate_max_drawdown(args.max_drawdown_pct)
    else:
        max_dd = record_k2_limit(repo_root) or DEFAULT_MAX_DRAWDOWN_PCT
    out = validate_out_path(args.out) if args.out else None

    report = build_report(
        trades=trades,
        candles=candles,
        entry_signals=entry_signals,
        order_counts=order_counts,
        notional=notional,
        timeframe=timeframe,
        pair=pair,
        source={
            "db": db_path,
            "mode": "dry_run" if config.get("dry_run", True) else "live",
            "strategy": config["strategy"],
            "candles": str(args.candles)
            if args.candles
            else "kraken public OHLC + persistent archive",
            "image": os.environ.get("SQ_FREQTRADE_IMAGE"),
            "strategy_sha256": strategy_hash,
            "config_sha256": config_hash,
            "order_counts_scope": "whole database; filled trades restricted to report window",
        },
        since=since,
        max_drawdown_pct=float(max_dd),
        trades_per_year_expected=record_trades_per_year(repo_root),
        signals_valid_from=signals_valid_from,
        acknowledged=[parse_utc_arg(value) for value in args.acknowledge or []],
        initial_capital=(
            args.initial_capital
            if args.initial_capital is not None
            else float(config["dry_run_wallet"])
            if config.get("dry_run", True) and since is None
            else None
        ),
    )
    text = json.dumps(report, indent=2, allow_nan=False)
    print(text)
    if out is not None:
        atomic_report(out, report)
    return EXIT_STOP if report["criteria"]["verdict"] == "STOP" else EXIT_CONTINUE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ReportInputError, market_data.CandleError) as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "action": "repair inputs and rerun; do not reset the window to hide failures",
                }
            )
        )
        sys.exit(EXIT_ERROR)
    except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
        # Match preflight/reconcile: never print str(exc) (it may echo config
        # paths or credential-adjacent text); name the exception class only.
        print(json.dumps({"error": f"{exc.__class__.__name__}: forward report failed"}, indent=2))
        sys.exit(EXIT_ERROR)
