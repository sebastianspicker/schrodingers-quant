"""The pure H1 simulator must reproduce the recorded Freqtrade trades.

This is the licence for every null model built on `sq.research.breakout`: if
the replay of H1's rules on the tracked proxy candles does not give the
recorded trades, nothing computed from synthetic paths means anything.
"""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from conftest import REPO_ROOT

from sq.research import breakout, proxy_data

RECORD = REPO_ROOT / "research" / "experiments" / "H1" / "equity-curves.json"
FEES = {"base": 0.005, "stress": 0.01}


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    return proxy_data.load_candles()


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORD.read_text())["runs"]


def test_proxy_candles_match_manifest(candles):
    manifest = json.loads((REPO_ROOT / "research" / "data-manifest.json").read_text())
    entry = next(e for e in manifest["entries"] if e["file"] == "BTC_EUR-4h.feather")
    assert len(candles) == entry["row_count"]
    assert candles["date"].iloc[0].isoformat() == entry["first_candle"]
    assert candles["date"].iloc[-1].isoformat() == entry["last_candle"]
    tracked = json.loads(proxy_data.MANIFEST_PATH.read_text())["entries"][0]
    assert tracked["file"] == "binance-BTC_EUR-4h.csv.gz"
    assert tracked["sha256"] == proxy_data.sha256()
    assert (tracked["row_count"], tracked["first_candle"], tracked["last_candle"]) == (
        entry["row_count"],
        entry["first_candle"],
        entry["last_candle"],
    )
    # The record's pinned manifest is byte-identical to what the H1 result files hash.
    pinned = hashlib.sha256(
        (REPO_ROOT / "research" / "data-manifest.json").read_bytes()
    ).hexdigest()
    record = (REPO_ROOT / "research" / "experiments" / "H1" / "record.md").read_text()
    assert pinned[:12] in record


@pytest.mark.parametrize(
    "key",
    [
        "train-base",
        "train-stress",
        "validation-base",
        "validation-stress",
        "heldout-base",
        "heldout-stress",
    ],
)
def test_simulator_reproduces_recorded_run(candles, record, key):
    run = record[key]
    fee = FEES[key.split("-")[1]]
    start, end = pd.Timestamp(run["start"], tz="UTC"), pd.Timestamp(run["end"], tz="UTC")
    trades = breakout.simulate(candles, start, end, fee)
    assert len(trades) == len(run["trades"])
    for simulated, recorded in zip(trades.itertuples(), run["trades"], strict=True):
        assert simulated.open_date.strftime("%Y-%m-%d %H:%M") == recorded["open"]
        assert simulated.close_date.strftime("%Y-%m-%d %H:%M") == recorded["close"]
        assert simulated.exit_reason == recorded["exit"]
        # The two price feeds differ in the last decimals of a few candles.
        assert simulated.profit_ratio * 100 == pytest.approx(recorded["return_pct"], abs=0.05)
    summary = breakout.summary(trades, candles, start, end)
    assert summary["net_return_pct"] == pytest.approx(run["net_return_pct"], abs=0.02)
    assert summary["mtm_max_drawdown_pct"] == pytest.approx(run["mtm_max_drawdown_pct"], abs=0.02)
    assert summary["stop_exits"] == 0


def _synthetic(closes: np.ndarray, start="2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="4h", tz="UTC")
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": 1.0,
        }
    )


def test_signals_exclude_current_candle():
    closes = np.full(200, 100.0)
    closes[150] = 101.0  # breaks the previous 120 highs on its own close
    frame = _synthetic(closes)
    enter, exit_ = breakout.signals(
        frame["high"].to_numpy(),
        frame["low"].to_numpy(),
        frame["close"].to_numpy(),
        breakout.Rules(),
    )
    assert enter[150] and not enter[151]
    assert not enter[:150].any()
    assert not exit_.any()


def test_entry_next_open_and_exit_signal_next_open():
    closes = np.full(400, 100.0)
    closes[200] = 110.0  # entry signal at 200, fill at 201 open (= close of 200)
    closes[201:260] = 110.0
    closes[260] = 90.0  # below the previous 60 lows: exit signal at 260, fill at 261 open
    closes[261:] = 90.0
    frame = _synthetic(closes)
    start, end = frame["date"].iloc[121], frame["date"].iloc[-1] + pd.Timedelta("4h")
    trades = breakout.simulate(frame, start, end, fee=0.0)
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["open_date"] == frame["date"].iloc[201]
    assert trade["open_rate"] == 110.0
    assert trade["close_date"] == frame["date"].iloc[261]
    assert trade["exit_reason"] == "exit_signal"
    assert trade["profit_ratio"] == pytest.approx(90 / 110 - 1)


def test_stop_loss_fills_at_stop_or_gap_open():
    closes = np.full(400, 100.0)
    closes[200] = 110.0
    closes[201:230] = 110.0
    closes[230] = 70.0  # the candle opens at 110 and falls through the −20 % stop (88)
    closes[231:] = 70.0
    frame = _synthetic(closes)
    start, end = frame["date"].iloc[121], frame["date"].iloc[-1] + pd.Timedelta("4h")
    trades = breakout.simulate(frame, start, end, fee=0.0)
    assert trades.iloc[0]["exit_reason"] == "stop_loss"
    assert trades.iloc[0]["close_rate"] == pytest.approx(88.0)
    assert trades.iloc[0]["close_date"] == frame["date"].iloc[230]

    # Now candle 230 opens at 60, below the stop: the fill is the open, not the stop.
    frame.loc[230, ["open", "high", "low", "close"]] = [60.0, 60.0, 60.0, 60.0]
    trades = breakout.simulate(frame, start, end, fee=0.0)
    assert trades.iloc[0]["exit_reason"] == "stop_loss"
    assert trades.iloc[0]["close_rate"] == pytest.approx(60.0)


def test_force_exit_at_window_end_and_fee_arithmetic():
    closes = np.full(300, 100.0)
    closes[250] = 120.0
    closes[251:] = 120.0
    frame = _synthetic(closes)
    start, end = frame["date"].iloc[121], frame["date"].iloc[-1] + pd.Timedelta("4h")
    trades = breakout.simulate(frame, start, end, fee=0.005)
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["exit_reason"] == "force_exit"
    assert trade["close_date"] == end
    assert trade["profit_ratio"] == pytest.approx((1 - 0.005) / (1 + 0.005) - 1)
    summary = breakout.summary(trades, frame, start, end)
    assert summary["trades"] == 1
    # The record's equity divides by the notional, not by the fee-inclusive open value.
    assert summary["net_return_pct"] == pytest.approx(trade["profit_abs"] / 1000 * 100)


def test_no_entry_from_signal_before_window():
    closes = np.full(300, 100.0)
    closes[150] = 110.0
    closes[151:] = 110.0
    frame = _synthetic(closes)
    # The window starts on the candle after the signal: Freqtrade's shifted
    # signal column is NaN on the first row, so no trade opens there.
    start, end = frame["date"].iloc[151], frame["date"].iloc[-1] + pd.Timedelta("4h")
    assert breakout.simulate(frame, start, end, fee=0.0).empty
    start = frame["date"].iloc[150]
    assert len(breakout.simulate(frame, start, end, fee=0.0)) == 1


def test_empty_window():
    frame = _synthetic(np.full(200, 100.0))
    trades = breakout.simulate(
        frame, pd.Timestamp("2030-01-01", tz="UTC"), pd.Timestamp("2030-02-01", tz="UTC"), 0.0
    )
    assert trades.empty


def test_stoploss_guard_locks_entries_after_two_stops():
    # Two breakouts that each fall through the stop, then a third breakout.
    closes = np.full(900, 100.0)
    for at, level in ((200, 110.0), (300, 120.0), (400, 130.0)):
        closes[at] = level  # each breakout exceeds the previous 120 highs
        closes[at + 1 : at + 10] = level
        closes[at + 10] = 70.0  # falls through the −20 % stop
        closes[at + 11 : at + 60] = 70.0
        closes[at + 60 :] = 100.0
    frame = _synthetic(closes)
    start, end = frame["date"].iloc[121], frame["date"].iloc[-1] + pd.Timedelta("4h")
    guarded = breakout.simulate(frame, start, end, fee=0.0)
    assert list(guarded["exit_reason"][:2]) == ["stop_loss", "stop_loss"]
    # The second stop closes at row 310; the guard locks until row 352, so the
    # breakout at row 400 (fill at 401) is still taken, but one at row 330 is not.
    unguarded = breakout.simulate(
        frame, start, end, fee=0.0, rules=breakout.Rules(stop_guard_limit=99)
    )
    assert len(guarded) == len(unguarded) == 3

    closes2 = closes.copy()
    closes2[330] = 125.0
    closes2[331:340] = 125.0
    closes2[340] = 70.0
    closes2[341:400] = 70.0
    frame2 = _synthetic(closes2)
    guarded2 = breakout.simulate(frame2, start, end, fee=0.0)
    unguarded2 = breakout.simulate(
        frame2, start, end, fee=0.0, rules=breakout.Rules(stop_guard_limit=99)
    )
    assert len(unguarded2) == len(guarded2) + 1
    assert guarded2.iloc[1]["close_date"] == frame2["date"].iloc[310]
    assert guarded2.iloc[2]["open_date"] == frame2["date"].iloc[401]
