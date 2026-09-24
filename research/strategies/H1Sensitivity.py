"""H1 sensitivity variants: alternate Turtle-style channel lengths, reported but
never used to select a strategy (research/hypotheses/H1.md, "Sensitivity").

Everything except the channel lengths (protections, stoploss, timeframe, order
types) is inherited unchanged from H1ChannelBreakout. Run only on train and
validation via `--strategy-path research/strategies`; never on the held-out
period.
"""

import sys
from pathlib import Path

# research/strategies and user_data/strategies are siblings under the repo
# root both on the host and inside the pinned image (research/ and user_data/
# are mounted as siblings under /freqtrade), so this relative lookup resolves
# in both places.
_REPO_ROOT_CANDIDATES = [
    Path(__file__).resolve().parents[2],
    Path("/freqtrade"),
]
for _root in _REPO_ROOT_CANDIDATES:
    _candidate = _root / "user_data" / "strategies"
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from H1ChannelBreakout import H1ChannelBreakout  # noqa: E402


class H1Sensitivity10x5(H1ChannelBreakout):
    """10-day entry / 5-day exit channel (60 / 30 candles at 4h)."""

    entry_channel = 60
    exit_channel = 30
    startup_candle_count = entry_channel + 1


class H1Sensitivity55x20(H1ChannelBreakout):
    """55-day entry / 20-day exit channel (330 / 120 candles at 4h)."""

    entry_channel = 330
    exit_channel = 120
    startup_candle_count = entry_channel + 1
