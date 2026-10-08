"""H2: H1's signals with volatility-targeted sizing (research/hypotheses/H2.md).

Entries and exits are inherited unchanged from H1ChannelBreakout. Each entry's
stake is scaled by clip(0.40 / sigma, 0.25, 1.0), where sigma is the annualised
standard deviation of the 120 log returns ending with the signal candle (the
last candle closed at decision time). A missing or non-positive sigma falls back to the
floor (fail small, never large). Freqtrade calls `custom_stake_amount` once per
entry, so the size is fixed at entry and never adjusted during a trade (H2.md,
"Sizing time"). Research only: reported on train and validation as a sanity
check, judged on forward data.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT_CANDIDATES = [
    Path(__file__).resolve().parents[2],
    Path("/freqtrade"),
]
for _root in _REPO_ROOT_CANDIDATES:
    _candidate = _root / "user_data" / "strategies"
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from H1ChannelBreakout import H1ChannelBreakout  # noqa: E402

# Strategies must not import `sq`, so the arithmetic below is a local copy of
# sq.research.h2 (the canonical one); tests/test_h2.py asserts they agree.
SIGMA_TARGET = 0.40
VOL_WINDOW = 120
STAKE_FLOOR = 0.25
STAKE_CAP = 1.0
CANDLES_PER_YEAR = 6 * 365


def realized_volatility(closes: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Annualised std of the `window` log returns ending with the row's own
    close (the row is a closed candle when it is read)."""
    log_returns = np.log(closes).diff()
    return log_returns.rolling(window).std(ddof=1) * np.sqrt(CANDLES_PER_YEAR)


def stake_fractions(sigma: pd.Series) -> pd.Series:
    """Vectorised clip(SIGMA_TARGET / sigma, floor, cap); NaN or <= 0 -> floor."""
    valid = sigma > 0
    ratio = SIGMA_TARGET / sigma.where(valid)
    return ratio.clip(STAKE_FLOOR, STAKE_CAP).where(valid, STAKE_FLOOR)


class H2VolTargetBreakout(H1ChannelBreakout):
    """H1 signals, stake scaled by inverse realized volatility."""

    # log returns need one extra candle, the shift another.
    startup_candle_count = max(H1ChannelBreakout.startup_candle_count, VOL_WINDOW + 2)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["h2_sigma"] = realized_volatility(dataframe["close"])
        dataframe["h2_stake_fraction"] = stake_fractions(dataframe["h2_sigma"])
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        # Use the last candle that closed before the entry candle opened: the
        # signal candle. In dry-run/live the analysed frame ends with that
        # candle; in backtesting it ends with the entry candle itself (whose
        # close is not yet known at entry), which the date filter excludes.
        # The same row is therefore used in both modes.
        fraction = STAKE_FLOOR
        dp = getattr(self, "dp", None)
        if dp is not None:
            frame, _ = dp.get_analyzed_dataframe(pair, self.timeframe)
            if frame is not None and len(frame) > 0:
                if current_time is not None and "date" in frame:
                    frame = frame[frame["date"] < current_time]
                if len(frame) > 0:
                    last = frame["h2_stake_fraction"].iloc[-1]
                    if not pd.isna(last):
                        fraction = float(last)
        stake = proposed_stake * fraction
        if min_stake is not None:
            stake = max(stake, min_stake)
        return min(stake, max_stake)
