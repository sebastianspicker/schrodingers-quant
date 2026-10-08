"""Growth, sizing and the growth-versus-drawdown frontier of a recorded run.

Pure numpy, so it runs on a development machine without the Freqtrade image.
It extends the desk report (`desk.py`), which scales the fixed stake, with the
question a trader asks next: what does the strategy do to an account that
*compounds* it?

The ergodicity point (Peters 2019). The ensemble average of the per-trade
return is what a fixed stake earns on average per trade, across many parallel
traders. A single trader who reinvests experiences the time-average growth,
the mean of ln(1 + r) per trade. The Jensen gap ln(1 + mean(r)) - mean(ln(1 + r)) is the volatility
drag, approximately sigma^2 / 2 for small returns. The arithmetic/log gap
mean(r) - mean(ln(1 + r)) also includes the change of units. A positive mean
trade can still accompany negative compounding growth.

What is reported:

- Per trade: mean, time-average growth, volatility drag.
- Kelly fraction: the stake share f in [0, f_max] that maximises the mean of
  ln(1 + f r) over the recorded trades (grid search; f_max = 1 is the
  project's no-leverage rule), with a percentile bootstrap of the trade list
  for its uncertainty and the share of resamples that say "do not trade" (f = 0).
  A wider search capped at 5 is reported so the cap's effect is visible;
  it is not a mathematically unconstrained optimum.
- Daily level: annualised mean and growth of the fixed-stake equity's own
  daily returns (E_t / E_{t-1} - 1, the convention of statistics.json).
- Frontier: for several fractions f, the compounding path
  E_t = E_{t-1} (1 + f r_t) with r_t the daily P&L on the fixed notional, its
  annualised growth, deepest drawdown and terminal multiple. Unlike the
  fixed-stake record, the stake grows and shrinks with the account. This is a
  retrospective rescaling of the recorded daily marks, not a new backtest (no
  new fills, fees or intraday path). The desk's cash sleeves are the
  fixed-stake, non-compounding analogue.

All of it describes the recorded path and its resamples. It does not establish
an edge and does not authorise capital.

Randomness uses the legacy `numpy.random.RandomState` with a fixed seed, so a
rebuilt report is reproducible exactly from the same input.
"""

import math
from datetime import date

import numpy as np

from sq.research.statistics import daily_returns, max_drawdown_pct

ANNUALISATION_DAYS = 365
DEFAULT_SEED = 20260924  # H1's predeclaration date
DEFAULT_RESAMPLES = 10_000
DEFAULT_GRID = 401
FRACTIONS = (0.25, 0.5, 0.75, 1.0)
UNCONSTRAINED_F_MAX = 5.0
_NEG_INF = -1e12  # stands in for ln(0) so zero-count terms do not produce NaN

QUALIFICATION = (
    "These figures describe the recorded path and its resamples; they do not establish "
    "an edge and do not authorise capital."
)


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(float(value), digits)


def _log_growth(returns: np.ndarray, fractions: np.ndarray) -> np.ndarray:
    """ln(1 + f r) for every trade (rows) and fraction (columns); a ruinous
    stake (1 + f r <= 0) gets a large negative value instead of -inf/NaN."""
    gross = 1 + np.outer(returns, fractions)
    safe = np.where(gross > 0, gross, 1.0)
    return np.where(gross > 0, np.log(safe), _NEG_INF)


# --- per trade ------------------------------------------------------------------


def trade_growth(returns_pct: list[float]) -> dict:
    """Ensemble average versus time-average growth of the per-trade returns
    (percent of stake). The Jensen gap is volatility drag; the arithmetic/log gap is separate."""
    r = np.asarray(returns_pct, dtype=float) / 100
    n = len(r)
    if n == 0:
        return {
            "n": 0,
            "mean_pct": None,
            "time_average_growth_pct": None,
            "ensemble_log_growth_pct": None,
            "arithmetic_log_gap_pct": None,
            "volatility_drag_pct": None,
        }
    mean = float(np.mean(r)) * 100
    if np.any(r <= -1):  # a total loss: the compounding account is wiped out
        return {
            "n": n,
            "mean_pct": _round(mean),
            "time_average_growth_pct": None,
            "ensemble_log_growth_pct": None,
            "arithmetic_log_gap_pct": None,
            "volatility_drag_pct": None,
        }
    time_average = float(np.mean(np.log1p(r))) * 100
    ensemble_log = float(np.log1p(np.mean(r))) * 100
    return {
        "n": n,
        "mean_pct": _round(mean),
        "time_average_growth_pct": _round(time_average),
        "ensemble_log_growth_pct": _round(ensemble_log),
        "arithmetic_log_gap_pct": _round(mean - time_average),
        "volatility_drag_pct": _round(ensemble_log - time_average),
    }


# --- Kelly ----------------------------------------------------------------------


def kelly_fraction(returns: np.ndarray, f_max: float = 1.0, grid: int = DEFAULT_GRID) -> float:
    """The fraction f in [0, f_max] that maximises the mean of ln(1 + f r) on a
    grid of `grid` points (returns are fractions, not percent). Ties go to the
    smaller fraction, so a non-positive growth rate everywhere gives 0."""
    r = np.asarray(returns, dtype=float)
    fractions = np.linspace(0.0, f_max, grid)
    mean_log = _log_growth(r, fractions).mean(axis=0)
    return float(fractions[int(np.argmax(mean_log))])


def kelly_fraction_unconstrained(
    returns: np.ndarray, f_max: float = UNCONSTRAINED_F_MAX, grid: int = DEFAULT_GRID
) -> float:
    """Same search with a loose cap, to report whether the optimum exceeds the
    no-leverage cap of 1."""
    return kelly_fraction(returns, f_max=f_max, grid=grid)


def _bootstrap_kelly(
    returns: np.ndarray, resamples: int, seed: int, f_max: float, grid: int
) -> np.ndarray:
    """Optimal capped fraction of each resampled trade list. The per-trade log
    growth is computed once; a resample's mean is its trade counts times it."""
    n = len(returns)
    fractions = np.linspace(0.0, f_max, grid)
    log_growth = _log_growth(returns, fractions)
    rng = np.random.RandomState(seed)
    idx = rng.randint(0, n, size=(resamples, n))
    counts = np.zeros((resamples, n))
    np.add.at(counts, (np.arange(resamples)[:, None], idx), 1.0)
    mean_log = counts @ log_growth / n
    return fractions[np.argmax(mean_log, axis=1)]


def kelly_report(
    returns_pct: list[float],
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    f_max: float = 1.0,
) -> dict:
    """Kelly fraction of the recorded trades with its bootstrap uncertainty.

    `kelly_ci95` is the percentile interval of the capped fraction over
    resampled trade lists; `share_resamples_zero` is the share of resamples
    whose optimum is 0 (the resample says do not trade).
    `growth_at_fractions_pct` is the mean ln(1 + f r) per trade, in percent."""
    r = np.asarray(returns_pct, dtype=float) / 100
    n = len(r)
    if n < 2:
        return {
            "n": n,
            "resamples": resamples,
            "seed": seed,
            "kelly_fraction": None,
            "kelly_fraction_unconstrained": None,
            "kelly_ci95": None,
            "share_resamples_zero": None,
            "growth_at_fractions_pct": None,
        }
    fractions = _bootstrap_kelly(r, resamples, seed, f_max, DEFAULT_GRID)
    at_fractions = {
        str(f): _round(float(_log_growth(r, np.array([f])).mean()) * 100) for f in FRACTIONS
    }
    return {
        "n": n,
        "resamples": resamples,
        "seed": seed,
        "f_max": f_max,
        "wider_search_f_max": UNCONSTRAINED_F_MAX,
        "kelly_fraction": _round(kelly_fraction(r, f_max)),
        "kelly_fraction_unconstrained": _round(kelly_fraction_unconstrained(r)),
        "kelly_ci95": [
            _round(float(np.percentile(fractions, 2.5))),
            _round(float(np.percentile(fractions, 97.5))),
        ],
        "share_resamples_zero": _round(float(np.mean(fractions == 0))),
        "growth_at_fractions_pct": at_fractions,
    }


# --- daily level ----------------------------------------------------------------


def _years(dates: list[str]) -> float:
    return (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365


def daily_growth(values: list[float], dates: list[str]) -> dict:
    """Annualised mean and time-average growth of the daily equity returns of the
    fixed-stake run (365 days per year); `years` spans the first to last mark."""
    r = daily_returns(values)
    mean = float(np.mean(r)) * ANNUALISATION_DAYS * 100
    growth = float(np.mean(np.log1p(r))) * ANNUALISATION_DAYS * 100
    ensemble_log = float(np.log1p(np.mean(r))) * ANNUALISATION_DAYS * 100
    return {
        "days": len(r),
        "years": _round(_years(dates)),
        "annualised_mean_pct": _round(mean),
        "annualised_growth_pct": _round(growth),
        "ensemble_log_growth_pct": _round(ensemble_log),
        "arithmetic_log_gap_pct": _round(mean - growth),
        "volatility_drag_pct": _round(ensemble_log - growth),
    }


def frontier(
    values: list[float], notional: float = 1000.0, fractions: tuple[float, ...] = FRACTIONS
) -> list[dict]:
    """Growth versus drawdown if a fraction f of the account were put behind the
    strategy and compounded daily: E_t = E_{t-1} (1 + f r_t), E_0 = 1, with r_t
    the daily change of the fixed-stake equity divided by the notional (the
    strategy's return on its stake, which a reinvesting account scales with its
    equity). A retrospective rescaling of the recorded marks, not a new
    backtest; the desk's cash sleeves are the fixed-stake (non-compounding)
    analogue."""
    v = np.asarray(values, dtype=float)
    r = np.diff(v) / notional
    out = []
    for f in fractions:
        path = np.concatenate(([1.0], np.cumprod(1 + f * r)))  # peak starts at 1
        out.append(
            {
                "fraction": f,
                "annualised_growth_pct": _round(
                    float(np.mean(np.log1p(f * r))) * ANNUALISATION_DAYS * 100
                ),
                "max_drawdown_pct": _round(max_drawdown_pct(path)),
                "terminal_multiple": _round(float(path[-1])),
            }
        )
    return out


# --- per run --------------------------------------------------------------------


def growth_report(run: dict, resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED) -> dict:
    """Growth block for one run of `equity-curves.json`."""
    trade_returns = [t["return_pct"] for t in run["trades"]]
    return {
        "trade_level": {
            **trade_growth(trade_returns),
            "kelly": kelly_report(trade_returns, resamples, seed),
        },
        "daily_level": daily_growth(run["strategy"], run["dates"]),
        "frontier": frontier(run["strategy"], float(run["strategy"][0])),
        "qualification": QUALIFICATION,
    }
