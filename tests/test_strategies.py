"""Tests for the frozen H1 strategy: signal arithmetic only (the Freqtrade
base class is a stand-in here, see conftest)."""

import hashlib

import numpy as np
import pandas as pd
import pytest
from conftest import REPO_ROOT, make_candles

from H1ChannelBreakout import H1ChannelBreakout

STRATEGY_FILE = REPO_ROOT / "user_data" / "strategies" / "H1ChannelBreakout.py"
FROZEN_SHA256 = "142f95a482cf41f03bf27ed071d5464c63b8abcfa64637b6a6608f5f70d7fc8e"
ENTRY, EXIT = 120, 60


def signals(frame: pd.DataFrame) -> pd.DataFrame:
    strategy = H1ChannelBreakout({})
    out = strategy.populate_indicators(frame.copy(), {})
    out = strategy.populate_entry_trend(out, {})
    return strategy.populate_exit_trend(out, {})


def random_walk(n: int = 500, seed: int = 1) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    closes = list(100 * np.cumprod(1 + rng.normal(0, 0.01, size=n)))
    return make_candles(closes, spread=0.3)


def test_frozen_file_hash():
    assert hashlib.sha256(STRATEGY_FILE.read_bytes()).hexdigest() == FROZEN_SHA256


def test_frozen_attributes():
    s = H1ChannelBreakout({})
    assert s.entry_channel == 120
    assert s.exit_channel == 60
    assert s.stoploss == -0.20
    assert s.timeframe == "4h"
    assert s.can_short is False
    assert s.minimal_roi == {"0": 100}
    assert s.startup_candle_count == 121
    assert s.process_only_new_candles is True
    assert s.trailing_stop is False
    assert s.use_exit_signal is True


def test_protections():
    assert H1ChannelBreakout({}).protections == [
        {"method": "CooldownPeriod", "stop_duration_candles": 1},
        {
            "method": "StoplossGuard",
            "lookback_period_candles": 180,
            "trade_limit": 2,
            "stop_duration_candles": 42,
            "only_per_pair": False,
        },
        {
            "method": "MaxDrawdown",
            "lookback_period_candles": 540,
            "trade_limit": 1,
            "max_allowed_drawdown": 0.25,
            "stop_duration_candles": 180,
        },
    ]


def test_channels_exclude_current_candle():
    frame = random_walk()
    out = signals(frame)
    for i in (ENTRY, 200, 333, 499):
        assert out["entry_high"].iloc[i] == frame["high"].iloc[i - ENTRY : i].max()
    for i in (EXIT, 200, 333, 499):
        assert out["exit_low"].iloc[i] == frame["low"].iloc[i - EXIT : i].min()


def test_warmup_rows_are_nan_and_silent():
    out = signals(random_walk())
    assert out["entry_high"].iloc[:ENTRY].isna().all()
    assert not np.isnan(out["entry_high"].iloc[ENTRY])
    assert (out["enter_long"].iloc[:ENTRY] == 0).all()
    assert out["exit_low"].iloc[:EXIT].isna().all()
    assert (out["exit_long"].iloc[:EXIT] == 0).all()


def test_breakout_signals_exactly_on_the_breakout_candle():
    closes = [100.0] * 300
    closes[200:] = [110.0] * 100
    out = signals(make_candles(closes))
    assert out.index[out["enter_long"] == 1].tolist() == [200]
    assert out["exit_long"].sum() == 0


def test_breakdown_signals_exit_on_the_breakdown_candle():
    closes = [100.0] * 300
    closes[200:] = [90.0] * 100
    out = signals(make_candles(closes))
    assert out.index[out["exit_long"] == 1].tolist() == [200]
    assert out["enter_long"].sum() == 0


def test_equal_to_channel_is_not_a_signal():
    # close == prior channel high / low: strict inequality, no signal.
    out = signals(make_candles([100.0] * 300))
    assert out["enter_long"].sum() == 0
    assert out["exit_long"].sum() == 0


@pytest.mark.parametrize("cut", [121, 150, 250, 377, 499])
def test_no_lookahead(cut):
    frame = random_walk()
    full = signals(frame)
    truncated = signals(frame.iloc[:cut])
    for column in ("entry_high", "exit_low", "enter_long", "exit_long"):
        pd.testing.assert_series_equal(
            full[column].iloc[:cut].reset_index(drop=True),
            truncated[column].reset_index(drop=True),
            check_names=False,
        )


def test_random_walk_produces_some_signals():
    out = signals(random_walk())
    assert out["enter_long"].sum() > 0
    assert out["exit_long"].sum() > 0
