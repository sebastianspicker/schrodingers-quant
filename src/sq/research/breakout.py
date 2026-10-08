"""A pure numpy/pandas reimplementation of H1's rules on 4h candles.

Why it exists: the null-model and calibration work (`sq.research.physics`)
must run H1's rules on thousands of synthetic or surrogate price paths, which
is impossible through Freqtrade's backtester on a workstation without Docker.
This module replays the frozen H1 rules as Freqtrade's backtester executes
them, and `tests/test_breakout.py` asserts that it reproduces every recorded
H1 trade (open, close, return) and the recorded marked-to-market figures on
the tracked proxy candles. If that test fails, the simulator may not be used
for a null model.

Semantics replicated from Freqtrade backtesting (fixed stake, one position):

- Indicators: `entry_high` is the max of the previous `entry_channel` highs,
  `exit_low` the min of the previous `exit_channel` lows (both exclude the
  current candle, as `H1ChannelBreakout` does with `shift(1)`).
- A signal on candle i's close acts at candle i+1's open (Freqtrade shifts
  signals by one candle). Signals from before the window's first candle are
  not acted on: the first entry can happen at the window's second candle.
- Exit signal (close below `exit_low`) acts at the next open and has priority
  over the stop in the same candle. The −20 % catastrophe stop is checked on
  every candle including the entry candle, fills at the stop price, or at the
  open when the candle opens through it. Positions still open at the end are
  force-exited at the last candle's close, with the close date one step later.
- Return per trade is Freqtrade's `profit_ratio`:
  close_rate (1 − fee) / (open_rate (1 + fee)) − 1, on a fixed stake.
- Protections. CooldownPeriod, StoplossGuard and MaxDrawdown follow the pinned
  Freqtrade 2026.8 backtester. Locks round to the next 4h boundary even when
  the nominal end is aligned. Lookbacks use strict close-date cutoffs and
  elapsed time, so missing candles do not extend protection durations.
  MaxDrawdown uses the default ratios mode: the largest absolute fall in
  cumulative closed-trade profit ratios over 540 candles, including an
  initial zero peak. A fall strictly above 0.25 locks for 180 candles; this
  is not a percentage decline in account equity. All closed exit types
  qualify, including signal exits. Synthetic and pinned-runtime contract
  tests cover protection boundaries not established by historical reproduction.

Nothing here reads files; `proxy_data.load_candles` provides candles.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

ENTRY_CHANNEL = 120
EXIT_CHANNEL = 60
STOPLOSS = -0.20
STEP = pd.Timedelta("4h")
NOTIONAL = 1000.0


@dataclass(frozen=True)
class Rules:
    entry_channel: int = ENTRY_CHANNEL
    exit_channel: int = EXIT_CHANNEL
    stoploss: float = STOPLOSS
    # StoplossGuard as configured in H1ChannelBreakout.protections.
    stop_guard_lookback: int = 180
    stop_guard_limit: int = 2
    stop_guard_duration: int = 42
    # MaxDrawdown uses Freqtrade's default "ratios" mode, not account drawdown.
    drawdown_lookback: int = 540
    drawdown_trade_limit: int = 1
    drawdown_limit: float = 0.25
    drawdown_duration: int = 180


H1_RULES = Rules()


def max_drawdown_lock_end(
    closed: list[tuple[pd.Timestamp, float]], now: pd.Timestamp, rules: Rules = H1_RULES
) -> pd.Timestamp | None:
    """Rounded MaxDrawdown unlock for chronologically ordered closed trades.

    Freqtrade 2026.8 defaults to ratios mode: the greatest absolute fall in
    cumulative profit ratios, with an initial zero peak. It neither compounds
    returns nor divides by the peak. Only close dates strictly inside the
    lookback qualify. The lock is based on the latest qualifying close.
    """
    cutoff = now - rules.drawdown_lookback * STEP
    recent = [(date, profit) for date, profit in closed if cutoff < date <= now]
    if len(recent) < rules.drawdown_trade_limit:
        return None
    cumulative = peak = drawdown = 0.0
    for _, profit in recent:
        cumulative += profit
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    if recent and drawdown > rules.drawdown_limit:
        return (recent[-1][0] + rules.drawdown_duration * STEP).floor(STEP) + STEP
    return None


def signals(high: np.ndarray, low: np.ndarray, close: np.ndarray, rules: Rules) -> tuple:
    """Boolean entry/exit signal arrays per candle close (NaN channels never signal)."""
    h = pd.Series(high)
    lo = pd.Series(low)
    entry_high = h.rolling(rules.entry_channel).max().shift(1).to_numpy()
    exit_low = lo.rolling(rules.exit_channel).min().shift(1).to_numpy()
    with np.errstate(invalid="ignore"):
        enter = close > entry_high
        exit_ = close < exit_low
    return enter, exit_


def simulate(
    candles: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    fee: float,
    rules: Rules = H1_RULES,
    notional: float = NOTIONAL,
) -> pd.DataFrame:
    """Trades Freqtrade's backtester would produce on `candles` in [start, end).

    `candles` must contain the warm-up history before `start`. Returns a
    DataFrame with `open_date`, `close_date`, `open_rate`, `close_rate`,
    `profit_ratio`, `profit_abs`, `exit_reason`, `amount`, `fee_open`,
    `fee_close` (the columns `metrics.equity_curve` consumes).
    """
    dates = pd.DatetimeIndex(candles["date"])
    o = candles["open"].to_numpy(dtype=float)
    h = candles["high"].to_numpy(dtype=float)
    lo = candles["low"].to_numpy(dtype=float)
    c = candles["close"].to_numpy(dtype=float)
    enter, exit_ = signals(h, lo, c, rules)

    in_window = (candles["date"] >= start) & (candles["date"] < end)
    idx = np.flatnonzero(in_window.to_numpy())
    rows: list[dict] = []
    if len(idx) == 0:
        return _frame(rows)
    w0, w1 = int(idx[0]), int(idx[-1])
    cursor = w0 + 1  # the first actionable signal is the window's first candle
    stop_closes: list[pd.Timestamp] = []
    closed: list[tuple[pd.Timestamp, float]] = []
    drawdown_locks = 0
    while cursor <= w1:
        hits = np.flatnonzero(enter[cursor - 1 : w1])
        if len(hits) == 0:
            break
        r = cursor + int(hits[0])
        open_rate = o[r]
        stop = open_rate * (1 + rules.stoploss)
        j = r
        close_rate = None
        reason = None
        while j <= w1:
            if j > r and exit_[j - 1]:
                close_rate, reason = o[j], "exit_signal"
                break
            if lo[j] <= stop:
                close_rate, reason = (o[j] if o[j] < stop else stop), "stop_loss"
                break
            j += 1
        if reason is None:
            j = w1
            close_rate, reason = c[w1], "force_exit"
            close_date = pd.Timestamp(dates[w1]) + STEP
        else:
            close_date = pd.Timestamp(dates[j])
        amount = notional / open_rate
        open_value = amount * open_rate * (1 + fee)
        close_value = amount * close_rate * (1 - fee)
        rows.append(
            {
                "open_date": pd.Timestamp(dates[r]),
                "close_date": close_date,
                "open_rate": open_rate,
                "close_rate": close_rate,
                "amount": amount,
                "fee_open": fee,
                "fee_close": fee,
                "profit_ratio": close_value / open_value - 1,
                "profit_abs": close_value - open_value,
                "exit_reason": reason,
            }
        )
        # PairLocks rounds the lock end to the NEXT candle boundary, including
        # already aligned dates. A one-candle cooldown therefore blocks j+1.
        # Freqtrade 2026.8 backtesting checks locks at the candle OPEN timestamp.
        unlock = (close_date + STEP).floor(STEP) + STEP
        if reason == "stop_loss":
            stop_closes.append(close_date)
            stop_closes = [
                date for date in stop_closes if date > close_date - rules.stop_guard_lookback * STEP
            ]
            if len(stop_closes) >= rules.stop_guard_limit:
                unlock = max(
                    unlock, (close_date + rules.stop_guard_duration * STEP).floor(STEP) + STEP
                )
        if reason != "force_exit":
            closed.append((close_date, rows[-1]["profit_ratio"]))
            closed = [
                item for item in closed if item[0] > close_date - rules.drawdown_lookback * STEP
            ]
            drawdown_end = max_drawdown_lock_end(closed, close_date, rules)
            if drawdown_end is not None:
                drawdown_locks += 1
                unlock = max(unlock, drawdown_end)
        # Use elapsed time, not row offsets: missing candles do not extend locks.
        cursor = max(j + 1, int(dates.searchsorted(unlock)))
    frame = _frame(rows)
    frame.attrs["max_drawdown_locks"] = drawdown_locks
    return frame


def _frame(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "open_date",
        "close_date",
        "open_rate",
        "close_rate",
        "amount",
        "fee_open",
        "fee_close",
        "profit_ratio",
        "profit_abs",
        "exit_reason",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        frame["open_date"] = pd.to_datetime(frame["open_date"], utc=True)
        frame["close_date"] = pd.to_datetime(frame["close_date"], utc=True)
    return frame


def equity(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    notional: float = NOTIONAL,
) -> pd.Series:
    """Marked-to-market equity per 4h close on the window, as the record defines it."""
    from sq.research.metrics import equity_curve

    window = candles[(candles["date"] >= start) & (candles["date"] < end)]
    return equity_curve(trades, window, notional)


def summary(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    notional: float = NOTIONAL,
) -> dict:
    """Net return, marked-to-market drawdown and trade count on the window."""
    from sq.research.metrics import max_drawdown_pct

    curve = equity(trades, candles, start, end, notional)
    return {
        "net_return_pct": (float(curve.iloc[-1]) / notional - 1) * 100,
        "mtm_max_drawdown_pct": max_drawdown_pct(curve, initial=notional),
        "trades": int(len(trades)),
        "stop_exits": int((trades["exit_reason"] == "stop_loss").sum()),
        "max_drawdown_locks": int(trades.attrs.get("max_drawdown_locks", 0)),
    }


def daily_marks(curve: pd.Series) -> pd.Series:
    """The last 4h mark of each UTC day (the basis of `statistics.json`)."""
    return curve.resample("1D").last().dropna()
