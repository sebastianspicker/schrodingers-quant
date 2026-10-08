"""Shared test setup.

The tests run on a development machine without Docker, so the pinned
Freqtrade image is not available. Everything under `sq` imports Freqtrade and
ccxt lazily; the strategy files, however, must subclass
`freqtrade.strategy.IStrategy` at import time. When Freqtrade is not
installed, this module registers a minimal stand-in package that provides just
enough of `IStrategy` to import the strategies and call their `populate_*`
methods on a DataFrame. Nothing in the stand-in reproduces Freqtrade
behaviour beyond holding class attributes; tests of the strategies therefore
cover their signal arithmetic, not Freqtrade's order handling.

When the real Freqtrade is importable (e.g. inside the image), the stand-in is
not installed and the same tests run against the real base class.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_freqtrade_stub() -> None:
    if importlib.util.find_spec("freqtrade") is not None:
        return

    freqtrade = types.ModuleType("freqtrade")
    freqtrade.__path__ = []  # mark as a package
    strategy_module = types.ModuleType("freqtrade.strategy")

    class IStrategy:
        """Minimal stand-in: Freqtrade's base class, reduced to what the
        project's strategies touch at import and signal-computation time."""

        INTERFACE_VERSION = 3
        timeframe = "4h"

        def __init__(self, config: dict | None = None) -> None:
            self.config = config or {}
            self.dp = None

    strategy_module.IStrategy = IStrategy
    freqtrade.strategy = strategy_module
    sys.modules["freqtrade"] = freqtrade
    sys.modules["freqtrade.strategy"] = strategy_module


_install_freqtrade_stub()


def make_candles(
    closes: list[float],
    *,
    start: str = "2025-01-01",
    timeframe_hours: int = 4,
    spread: float = 0.0,
) -> pd.DataFrame:
    """Synthetic OHLCV candles from a close series: open is the previous
    close, high/low wrap close by `spread` (absolute)."""
    dates = pd.date_range(start, periods=len(closes), freq=f"{timeframe_hours}h", tz="UTC")
    closes_s = pd.Series(closes, dtype=float)
    opens = closes_s.shift(1).fillna(closes_s.iloc[0])
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": opens.to_numpy(),
            "high": (pd.concat([opens, closes_s], axis=1).max(axis=1) + spread).to_numpy(),
            "low": (pd.concat([opens, closes_s], axis=1).min(axis=1) - spread).to_numpy(),
            "close": closes_s.to_numpy(),
            "volume": 1.0,
        }
    )
    return frame


@pytest.fixture
def candles_factory():
    return make_candles
