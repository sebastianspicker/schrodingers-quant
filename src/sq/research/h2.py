"""The H2 experiment definition (research/hypotheses/H2.md): H1's signals with
volatility-targeted sizing. The sizing arithmetic is pure (numpy/pandas only)
so offline evaluation of the forward window and the strategy share one rule.
`research/strategies/H2VolTargetBreakout.py` keeps a local copy because
strategies must not import `sq`; a test asserts the two agree.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from sq.research import h1, metrics

SIGMA_TARGET = 0.40
VOL_WINDOW = 120
STAKE_FLOOR = 0.25
STAKE_CAP = 1.0
CANDLES_PER_YEAR = 6 * 365

RECORD_DIR = Path("/freqtrade/research/experiments/H2")
STRATEGY_PATH = Path("/freqtrade/research/strategies")
STRATEGY_FILE = STRATEGY_PATH / "H2VolTargetBreakout.py"


def realized_volatility(closes: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Annualised std (ddof=1) of the `window` log returns ending with row i's
    own close. Row i is read only once its candle has closed (the signal
    candle), so this uses exactly the information available at decision time
    and nothing from the entry candle; see `H2VolTargetBreakout.custom_stake_amount`
    for how the signal candle's row is selected in both live and backtest mode."""
    log_returns = np.log(closes).diff()
    return log_returns.rolling(window).std(ddof=1) * np.sqrt(CANDLES_PER_YEAR)


def stake_fraction(
    sigma_realized: float,
    *,
    sigma_target: float = SIGMA_TARGET,
    floor: float = STAKE_FLOOR,
    cap: float = STAKE_CAP,
) -> float:
    """clip(sigma_target / sigma_realized, floor, cap). A missing (NaN) or
    non-positive volatility returns `floor`: fail small, never large."""
    if np.isnan(sigma_realized) or sigma_realized <= 0:
        return floor
    return float(np.clip(sigma_target / sigma_realized, floor, cap))


def _runs(period: str, start: str, end: str) -> list[h1.BacktestRun]:
    return [
        h1.BacktestRun(
            name=f"h2-{period}-{label}-BTC_EUR",
            period=period,
            pair=h1.PAIR,
            strategy="H2VolTargetBreakout",
            strategy_path=STRATEGY_PATH,
            strategy_file=STRATEGY_FILE,
            start=start,
            end=end,
            fee=fee,
        )
        for label, fee in (("base", h1.FEE_BASE), ("stress", h1.FEE_STRESS))
    ]


TRAIN_RUNS = _runs("train", h1.TRAIN_START, h1.TRAIN_END)
VALIDATION_RUNS = _runs("validation", h1.VALIDATION_START, h1.VALIDATION_END)

# No held-out runs: the 2024-07 -> 2026-09 window is spent (ADR-0004) and H2 is
# judged on forward data only (H2.md, "Evaluation").

# --- paired forward evaluation ----------------------------------------------------

RATIO_MARGIN = 0.2  # H2-2: H2's return / max drawdown must exceed H1's by this much


def resize_trades(trades: pd.DataFrame, candles: pd.DataFrame, step: pd.Timedelta) -> pd.DataFrame:
    """H2's trades from H1's: the same trades with `amount` and `profit_abs`
    scaled by the stake fraction at the signal candle (the last candle that
    closed before the entry candle opened). `trades` has the columns
    `metrics.equity_curve` needs, with candle-aligned `open_date`."""
    sigma = pd.Series(
        realized_volatility(candles["close"]).to_numpy(), index=pd.DatetimeIndex(candles["date"])
    )
    out = trades.copy()
    fractions = []
    for open_date in out.get("decision_date", out["open_date"]):
        signal_candle = pd.Timestamp(open_date).floor(step) - step
        value = sigma.get(signal_candle, np.nan)
        fractions.append(stake_fraction(float(value)))
    out["h2_stake_fraction"] = fractions
    out["amount"] = out["amount"] * out["h2_stake_fraction"]
    out["profit_abs"] = out["profit_abs"] * out["h2_stake_fraction"]
    return out


def _summary(equity: pd.Series, notional: float) -> dict:
    net = (float(equity.iloc[-1]) / notional - 1) * 100
    dd = metrics.max_drawdown_pct(equity, initial=notional)
    if dd < 1e-10:  # Roundoff from balance + exit value - entry value is not risk.
        dd = 0.0
    return {
        "net_return_pct": round(net, 4),
        "mtm_max_drawdown_pct": round(dd, 4),
        "return_over_max_drawdown": round(net / dd, 4) if dd > 0 else None,
    }


def paired_evaluation(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    notional: float,
    step: pd.Timedelta,
    *,
    sizing_candles: pd.DataFrame | None = None,
    closed_trades: int | None = None,
    stopped: bool = False,
    evidence_valid: bool = False,
) -> dict:
    """H2.md's decision rule on one window: H1's trades against the same trades
    re-sized, both marked to market on the fixed notional. The caller decides
    whether the sample is large enough (forward protocol F4)."""
    history = candles if sizing_candles is None else sizing_candles
    resized = resize_trades(trades, history, step)
    sigma = pd.Series(realized_volatility(history["close"]).to_numpy(), index=history["date"])
    missing = sum(
        not np.isfinite(sigma.get(pd.Timestamp(t).floor(step) - step, np.nan))
        for t in trades.get("decision_date", trades["open_date"])
    )
    h1_summary = _summary(metrics.equity_curve(trades, candles, notional), notional)
    h2_summary = _summary(metrics.equity_curve(resized, candles, notional), notional)
    h1_ratio, h2_ratio = (
        h1_summary["return_over_max_drawdown"],
        h2_summary["return_over_max_drawdown"],
    )
    if h2_ratio is None:
        ratio_pass = h1_ratio is not None and h2_summary["net_return_pct"] > 0
    elif h1_ratio is None:
        ratio_pass = h2_summary["net_return_pct"] > 0
    else:
        ratio_pass = h2_ratio >= h1_ratio + RATIO_MARGIN
    criteria = [
        {
            "id": "H2-1",
            "description": "H2 max drawdown <= H1 max drawdown",
            "pass": h2_summary["mtm_max_drawdown_pct"] <= h1_summary["mtm_max_drawdown_pct"],
        },
        {
            "id": "H2-2",
            "description": f"H2 return / max drawdown >= H1's + {RATIO_MARGIN}",
            "pass": bool(ratio_pass),
        },
        {
            "id": "H2-3",
            "description": "H2 net return > 0",
            "pass": h2_summary["net_return_pct"] > 0,
        },
    ]
    sample = closed_trades if closed_trades is not None else 0
    eligible = (
        evidence_valid
        and missing == 0
        and sample >= (20 if stopped else 30)
        and not (h1_ratio is None and h2_ratio is None)
    )
    all_pass = all(c["pass"] for c in criteria)
    return {
        "trades": int(len(trades)),
        "notional": notional,
        "closed_trades": sample,
        "missing_sizing_history": missing,
        "evidence_valid": evidence_valid,
        "judgement_allowed": eligible,
        "verdict": ("GO" if all_pass else "NO_GO") if eligible else "INCONCLUSIVE",
        "stake_fractions": [round(float(f), 4) for f in resized["h2_stake_fraction"]],
        "h1": h1_summary,
        "h2": h2_summary,
        "criteria": criteria,
        "all_pass": all_pass,
    }
