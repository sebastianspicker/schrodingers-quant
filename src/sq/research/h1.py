"""The H1 experiment definition (research/hypotheses/H1.md): periods, fees,
pair, strategy locations and output paths, in one place so every research
subcommand reads the same numbers. The dates and fees are predeclared in
H1.md; changing them means a new hypothesis, not an edit.
"""

from dataclasses import dataclass
from pathlib import Path

PAIR = "BTC/EUR"
ETH_PAIR = "ETH/EUR"
TIMEFRAME = "4h"

TRAIN_START = "2020-01-01"
TRAIN_END = "2022-12-31"
VALIDATION_START = "2023-01-01"
VALIDATION_END = "2024-06-30"
HELDOUT_START = "2024-07-01"
HELDOUT_END = "2026-09-20"

FEE_BASE = 0.005
FEE_STRESS = 0.01

NOTIONAL = 1000.0

# lookahead-analysis/recursive-analysis probe a short window right at the
# timerange start; the Binance BTC/EUR feed only begins 2020-01-03, so start a
# few days later to give that probe window real candles. This only affects the
# bias check's internal window, not any reported period.
BIAS_START = "2020-01-10"

CONFIG = Path("/freqtrade/research/configs/backtest.json")
RECORD_DIR = Path("/freqtrade/research/experiments/H1")
BACKTEST_ROOT = Path("/freqtrade/user_data/backtest_results/H1")
DATADIR = Path("/freqtrade/user_data/data/binance")
MANIFEST = Path("/freqtrade/research/data-manifest.json")

H1_STRATEGY_PATH = Path("/freqtrade/strategies")
H1_STRATEGY_FILE = H1_STRATEGY_PATH / "H1ChannelBreakout.py"
SENSITIVITY_STRATEGY_PATH = Path("/freqtrade/research/strategies")
SENSITIVITY_STRATEGY_FILE = SENSITIVITY_STRATEGY_PATH / "H1Sensitivity.py"


@dataclass(frozen=True)
class BacktestRun:
    """One `freqtrade backtesting` invocation and its recorded summary.

    `name` is both the `--backtest-directory` leaf under BACKTEST_ROOT and the
    stem of the recorded `<name>.json` / `mtm-<name>.json` files under
    RECORD_DIR.
    """

    name: str
    period: str
    pair: str
    strategy: str
    strategy_path: Path
    strategy_file: Path
    start: str
    end: str
    fee: float


TRAIN_RUNS = [
    BacktestRun(
        name="train-base-BTC_EUR",
        period="train",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=TRAIN_START,
        end=TRAIN_END,
        fee=FEE_BASE,
    ),
    BacktestRun(
        name="train-stress-BTC_EUR",
        period="train",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=TRAIN_START,
        end=TRAIN_END,
        fee=FEE_STRESS,
    ),
]

VALIDATION_RUNS = [
    BacktestRun(
        name="validation-base-BTC_EUR",
        period="validation",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=VALIDATION_START,
        end=VALIDATION_END,
        fee=FEE_BASE,
    ),
    BacktestRun(
        name="validation-stress-BTC_EUR",
        period="validation",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=VALIDATION_START,
        end=VALIDATION_END,
        fee=FEE_STRESS,
    ),
]

# Sensitivity and the ETH robustness check are reported once over the
# combined train+validation span (H1.md: "on train + validation only"), never
# on the held-out period, and never used to select a strategy.
SENSITIVITY_RUNS = [
    BacktestRun(
        name="sensitivity-10x5-BTC_EUR",
        period="sensitivity-10x5",
        pair=PAIR,
        strategy="H1Sensitivity10x5",
        strategy_path=SENSITIVITY_STRATEGY_PATH,
        strategy_file=SENSITIVITY_STRATEGY_FILE,
        start=TRAIN_START,
        end=VALIDATION_END,
        fee=FEE_BASE,
    ),
    BacktestRun(
        name="sensitivity-55x20-BTC_EUR",
        period="sensitivity-55x20",
        pair=PAIR,
        strategy="H1Sensitivity55x20",
        strategy_path=SENSITIVITY_STRATEGY_PATH,
        strategy_file=SENSITIVITY_STRATEGY_FILE,
        start=TRAIN_START,
        end=VALIDATION_END,
        fee=FEE_BASE,
    ),
]

ETH_ROBUSTNESS_RUNS = [
    BacktestRun(
        name="eth-robustness-train-validation",
        period="eth-robustness",
        pair=ETH_PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=TRAIN_START,
        end=VALIDATION_END,
        fee=FEE_BASE,
    ),
]

# DEFINED BUT MUST NOT BE RUN before the strategy is frozen; run once, after
# the strategy is frozen (research/hypotheses/H1.md).
HELDOUT_RUNS = [
    BacktestRun(
        name="heldout-base-BTC_EUR",
        period="heldout",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=HELDOUT_START,
        end=HELDOUT_END,
        fee=FEE_BASE,
    ),
    BacktestRun(
        name="heldout-stress-BTC_EUR",
        period="heldout",
        pair=PAIR,
        strategy="H1ChannelBreakout",
        strategy_path=H1_STRATEGY_PATH,
        strategy_file=H1_STRATEGY_FILE,
        start=HELDOUT_START,
        end=HELDOUT_END,
        fee=FEE_STRESS,
    ),
]

BENCHMARK_PERIODS = {
    "train": (TRAIN_START, TRAIN_END),
    "validation": (VALIDATION_START, VALIDATION_END),
    "heldout": (HELDOUT_START, HELDOUT_END),
}
