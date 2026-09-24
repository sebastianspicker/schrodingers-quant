"""Long-only channel-breakout trend follower for BTC/EUR 4h, per research/hypotheses/H1.md.

Rules are predeclared and fixed; do not tune parameters after the hypothesis
freeze date without writing a new hypothesis (H2, ...). Entry/exit channel
lengths are class attributes so research subclasses (see
research/strategies/H1Sensitivity.py) can vary them for the reported
sensitivity check without duplicating this file.
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame


class H1ChannelBreakout(IStrategy):
    """H1: 20-day/10-day (120/60 candle) channel breakout, long only, 4h candles."""

    INTERFACE_VERSION = 3
    can_short = False
    timeframe = "4h"
    process_only_new_candles = True

    # Channel lengths in candles (20 days / 10 days at 4h). Subclasses may
    # override these together with startup_candle_count for the sensitivity
    # check; the values below are H1's fixed, predeclared rules.
    entry_channel = 120
    exit_channel = 60
    startup_candle_count = entry_channel + 1

    # No ROI table: an unreachable ROI target disables ROI-based exits in
    # practice (freqtrade.strategy.interface.min_roi_reached_entry finds the
    # highest duration-eligible entry, so 10000% profit at minute 0 is never
    # reached). The catastrophe stop and the exit signal are the only exits.
    minimal_roi = {"0": 100}
    stoploss = -0.20
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "emergency_exit": "market",
        "stoploss": "market",
        # Overridden by the live overlay (Slice C); backtests/dry-run keep the
        # stop in the bot so research fills stay comparable across profiles.
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    @property
    def protections(self) -> list[dict]:
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 1},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 180,  # 30 days at 4h
                "trade_limit": 2,
                "stop_duration_candles": 42,  # 7 days at 4h
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 540,  # 90 days at 4h
                "trade_limit": 1,
                "max_allowed_drawdown": 0.25,
                "stop_duration_candles": 180,  # 30 days at 4h
            },
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # shift(1) excludes the current candle from its own channel, matching
        # H1's "previous N candles" wording exactly.
        dataframe["entry_high"] = dataframe["high"].rolling(self.entry_channel).max().shift(1)
        dataframe["exit_low"] = dataframe["low"].rolling(self.exit_channel).min().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe.loc[dataframe["close"] > dataframe["entry_high"], "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe.loc[dataframe["close"] < dataframe["exit_low"], "exit_long"] = 1
        return dataframe
