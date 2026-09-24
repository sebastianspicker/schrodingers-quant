"""Signal, history and no-lookahead tests for H1ChannelBreakout (research/hypotheses/H1.md).

These are synthetic-dataframe unit tests of the populate_* callbacks; they do
not exercise the Freqtrade backtester or touch real market data.
"""

import numpy as np
import pandas as pd
import pytest

from H1ChannelBreakout import H1ChannelBreakout


def make_flat_ohlcv(n: int, price: float = 100.0) -> pd.DataFrame:
    """A flat OHLCV dataframe (no breakouts anywhere) to be mutated by tests."""
    return pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC"),
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1.0,
        }
    )


def run_pipeline(strategy: H1ChannelBreakout, dataframe: pd.DataFrame) -> pd.DataFrame:
    df = strategy.populate_indicators(dataframe.copy(), {"pair": "BTC/EUR"})
    df = strategy.populate_entry_trend(df, {"pair": "BTC/EUR"})
    df = strategy.populate_exit_trend(df, {"pair": "BTC/EUR"})
    return df


@pytest.fixture
def strategy() -> H1ChannelBreakout:
    return H1ChannelBreakout({"stake_currency": "EUR"})


def test_entry_fires_on_close_above_prior_high_excluding_current_candle(strategy):
    # entry_channel prior candles flat at 100, then one breakout candle whose
    # own (huge) high must NOT count toward the comparison window: only the
    # close vs. the *prior* 120-candle high matters.
    n = strategy.entry_channel + 1
    df = make_flat_ohlcv(n)
    df.loc[n - 1, "close"] = 101.0
    df.loc[n - 1, "high"] = 1000.0  # would poison an unshifted rolling max

    result = run_pipeline(strategy, df)

    assert result.loc[n - 1, "enter_long"] == 1
    assert result.loc[: n - 2, "enter_long"].eq(0).all()


def test_no_entry_when_close_does_not_exceed_prior_high(strategy):
    n = strategy.entry_channel + 1
    df = make_flat_ohlcv(n)
    df.loc[n - 1, "close"] = 99.0

    result = run_pipeline(strategy, df)

    assert result["enter_long"].eq(0).all()


def test_exit_fires_on_close_below_prior_low_excluding_current_candle(strategy):
    n = strategy.exit_channel + 1
    df = make_flat_ohlcv(n)
    df.loc[n - 1, "close"] = 99.0
    df.loc[n - 1, "low"] = 1.0  # would poison an unshifted rolling min

    result = run_pipeline(strategy, df)

    assert result.loc[n - 1, "exit_long"] == 1
    assert result.loc[: n - 2, "exit_long"].eq(0).all()


def test_no_exit_when_close_does_not_fall_below_prior_low(strategy):
    n = strategy.exit_channel + 1
    df = make_flat_ohlcv(n)
    df.loc[n - 1, "close"] = 101.0

    result = run_pipeline(strategy, df)

    assert result["exit_long"].eq(0).all()


def test_no_signal_before_enough_history(strategy):
    # Fewer rows than startup_candle_count: the rolling windows are never
    # full, so entry_high/exit_low stay NaN and no signal can fire, even with
    # an extreme close on the final row.
    n = strategy.entry_channel  # one short of startup_candle_count
    df = make_flat_ohlcv(n)
    df.loc[n - 1, "close"] = 1_000_000.0

    result = run_pipeline(strategy, df)

    assert result["enter_long"].eq(0).all()
    assert result["exit_long"].eq(0).all()


def test_no_lookahead_signal_unchanged_when_future_rows_altered_or_removed(strategy):
    n = strategy.entry_channel + 40
    rng = np.random.default_rng(seed=1234)
    df = make_flat_ohlcv(n)
    # A deterministic, non-trivial price path so the channel is non-flat.
    walk = np.cumsum(rng.normal(loc=0.0, scale=1.0, size=n))
    df["close"] = 100.0 + walk
    df["open"] = df["close"]
    df["high"] = df["close"] + rng.uniform(0.0, 2.0, size=n)
    df["low"] = df["close"] - rng.uniform(0.0, 2.0, size=n)

    cutoff = strategy.entry_channel + 10  # last row that must be lookahead-stable
    full_result = run_pipeline(strategy, df)

    # (a) truncate rows after cutoff entirely
    truncated = df.iloc[: cutoff + 1].copy()
    truncated_result = run_pipeline(strategy, truncated)
    pd.testing.assert_series_equal(
        full_result.loc[:cutoff, "enter_long"],
        truncated_result.loc[:cutoff, "enter_long"],
    )
    pd.testing.assert_series_equal(
        full_result.loc[:cutoff, "exit_long"],
        truncated_result.loc[:cutoff, "exit_long"],
    )

    # (b) keep the same length but overwrite every row after cutoff
    altered = df.copy()
    altered.loc[cutoff + 1 :, ["open", "high", "low", "close"]] = 1.0
    altered_result = run_pipeline(strategy, altered)
    pd.testing.assert_series_equal(
        full_result.loc[:cutoff, "enter_long"],
        altered_result.loc[:cutoff, "enter_long"],
    )
    pd.testing.assert_series_equal(
        full_result.loc[:cutoff, "exit_long"],
        altered_result.loc[:cutoff, "exit_long"],
    )


def test_protections_match_h1_values(strategy):
    assert strategy.protections == [
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


def test_fixed_risk_parameters_match_h1():
    assert H1ChannelBreakout.stoploss == -0.20
    assert H1ChannelBreakout.use_exit_signal is True
    assert H1ChannelBreakout.trailing_stop is False
    assert H1ChannelBreakout.timeframe == "4h"
    assert H1ChannelBreakout.can_short is False
    assert H1ChannelBreakout.process_only_new_candles is True
    assert H1ChannelBreakout.entry_channel == 120
    assert H1ChannelBreakout.exit_channel == 60
    assert H1ChannelBreakout.startup_candle_count >= 121
