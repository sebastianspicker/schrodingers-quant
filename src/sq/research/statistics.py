"""Statistical assessment of a recorded experiment, for the record and the demo.

Reads an experiment's `equity-curves.json` (daily marked-to-market equity of
the strategy and of buy-and-hold on the fixed notional, plus the trade list;
see `equity_curves.py`) and writes `statistics.json` next to it. Everything
here is pure numpy/pandas, so it runs on a development machine without the
Freqtrade image: `make stats`.

What is reported per run, and why a trader wants it:

- Return and risk from the daily series: annualised volatility, Sharpe and
  Sortino (risk-free rate 0, 365-day annualisation), Calmar, beta and
  correlation to buy-and-hold, longest drawdown. The same figures for
  buy-and-hold.
- Trade statistics: win rate, mean and standard deviation of the per-trade
  return, its t-statistic, profit factor, payoff ratio, concentration (how
  much of the result the two largest trades carry), longest losing streak.
- Uncertainty, not just point estimates. A percentile bootstrap of the trades
  gives a 95 % interval for the mean trade and for the sum of trade returns,
  and the share of resamples with a non-positive mean. A circular block
  bootstrap of the daily returns (fixed-length blocks) gives an interval for
  the Sharpe ratio.
- Power: how many trades, and therefore years at the observed trade
  frequency, it would take for a mean trade of this size to reach t = 2.
- Two benchmarks that buy-and-hold at 100 % exposure does not provide:
  * Random timing with the same exposure: the strategy's own trade durations
    are placed at random, non-overlapping positions in the period, with the
    same fees; the distribution of return and drawdown over many trials says
    whether *when* the strategy was long added anything beyond *how long* it
    was long.
  * Constant partial exposure: holding the strategy's average exposure in the
    asset every day (daily rebalanced, no rebalancing cost), the simplest
    passive portfolio with the same market participation.
- Calendar-year returns of the strategy and of buy-and-hold.
- The predeclared decision rule (K1–K4), recomputed from the recorded headline
  figures with its margins, so the record and the demo cannot disagree.

Randomness uses the legacy `numpy.random.RandomState`, whose stream is
guaranteed stable across numpy versions, so a rebuilt `statistics.json` is
reproducible exactly from the same `equity-curves.json`.

Drawdowns of the benchmarks are computed on the daily marks, so they are
compared with the strategy's drawdown on the same daily marks
(`max_drawdown_pct_daily`), not with the recorded 4h figure, which is deeper.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ANNUALISATION_DAYS = 365
DEFAULT_SEED = 20260924  # H1's predeclaration date
DEFAULT_TRADE_RESAMPLES = 10_000
DEFAULT_SHARPE_RESAMPLES = 2_000
DEFAULT_BLOCK_DAYS = 20
DEFAULT_TIMING_TRIALS = 5_000
T_TARGET = 2.0
K2_FACTOR = 0.6  # H1.md: held-out drawdown <= 0.6 x buy-and-hold's

# Cost per side for the runs the demo shows, keyed by the `-<cost>` suffix of
# the run key (`equity_curves.COST_LABELS` inverted).
COST_PER_SIDE = {"base": 0.005, "stress": 0.01}


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round for the record; NaN (undefined ratio) becomes None (JSON null)."""
    if value is None or math.isnan(value):
        return None
    return round(float(value), digits)


# --- daily series ----------------------------------------------------------


def daily_returns(values: list[float] | np.ndarray) -> np.ndarray:
    """Simple returns between consecutive daily marks."""
    v = np.asarray(values, dtype=float)
    return v[1:] / v[:-1] - 1


def annualised_volatility_pct(returns: np.ndarray) -> float:
    return float(np.std(returns, ddof=1) * math.sqrt(ANNUALISATION_DAYS) * 100)


def sharpe_ratio(returns: np.ndarray) -> float:
    """Annualised Sharpe ratio with a zero risk-free rate; NaN if flat."""
    sd = np.std(returns, ddof=1)
    if sd == 0:
        return float("nan")
    return float(np.mean(returns) / sd * math.sqrt(ANNUALISATION_DAYS))


def sortino_ratio(returns: np.ndarray) -> float:
    """Annualised Sortino ratio (downside deviation against 0); NaN if no downside."""
    downside = math.sqrt(float(np.mean(np.minimum(returns, 0.0) ** 2)))
    if downside == 0:
        return float("nan")
    return float(np.mean(returns) / downside * math.sqrt(ANNUALISATION_DAYS))


def beta_and_correlation(returns: np.ndarray, benchmark: np.ndarray) -> tuple[float, float]:
    if len(returns) != len(benchmark):
        raise ValueError("series lengths differ")
    cov = float(np.cov(returns, benchmark, ddof=1)[0, 1])
    var_b = float(np.var(benchmark, ddof=1))
    sd_r = float(np.std(returns, ddof=1))
    beta = cov / var_b if var_b else float("nan")
    corr = cov / (sd_r * math.sqrt(var_b)) if sd_r and var_b else float("nan")
    return beta, corr


def max_drawdown_pct(values: list[float] | np.ndarray) -> float:
    v = np.asarray(values, dtype=float)
    peaks = np.maximum.accumulate(v)
    return float(np.max((peaks - v) / peaks) * 100)


def longest_drawdown_days(values: list[float] | np.ndarray) -> int:
    """Longest stretch of consecutive days below the previous running peak,
    including an unrecovered drawdown that runs to the end of the series."""
    v = np.asarray(values, dtype=float)
    peaks = np.maximum.accumulate(v)
    under = v < peaks
    longest = run = 0
    for flag in under:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return int(longest)


# --- trades ----------------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    open: pd.Timestamp
    close: pd.Timestamp
    return_pct: float
    exit: str

    @property
    def hours(self) -> float:
        return (self.close - self.open).total_seconds() / 3600


def parse_trades(records: list[dict]) -> list[Trade]:
    """Trades as written by `equity_curves.build_run` ("YYYY-MM-DD HH:MM" UTC)."""
    return [
        Trade(
            open=pd.Timestamp(r["open"], tz="UTC"),
            close=pd.Timestamp(r["close"], tz="UTC"),
            return_pct=float(r["return_pct"]),
            exit=str(r["exit"]),
        )
        for r in records
    ]


def exposure_pct(trades: list[Trade], start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Share of the period [start, end) spent in a position, by time."""
    total = (end - start).total_seconds()
    if total <= 0:
        raise ValueError("period must be non-empty")
    held = sum(max(0.0, (min(t.close, end) - max(t.open, start)).total_seconds()) for t in trades)
    return 100 * held / total


def longest_losing_streak(returns_pct: list[float]) -> int:
    longest = run = 0
    for r in returns_pct:
        run = run + 1 if r <= 0 else 0
        longest = max(longest, run)
    return longest


def trade_statistics(trades: list[Trade]) -> dict:
    """Descriptive statistics of the per-trade net returns (percent of stake)."""
    r = np.array([t.return_pct for t in trades], dtype=float)
    n = len(r)
    if n == 0:
        return {"count": 0}
    wins = r[r > 0]
    losses = r[r <= 0]
    mean = float(np.mean(r))
    sd = float(np.std(r, ddof=1)) if n > 1 else float("nan")
    t_stat = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    ranked = np.sort(r)[::-1]
    top2 = float(np.sum(ranked[:2]))
    hours = sorted(t.hours for t in trades)
    return {
        "count": n,
        "winners": int(len(wins)),
        "win_rate_pct": round(100 * len(wins) / n, 2),
        "mean_pct": _round(mean),
        "sd_pct": _round(sd),
        "t_stat": _round(t_stat),
        "sum_pct": round(float(np.sum(r)), 4),
        "profit_factor": (
            round(float(np.sum(wins) / -np.sum(losses)), 4)
            if len(losses) and np.sum(losses) < 0
            else None
        ),
        "mean_win_pct": round(float(np.mean(wins)), 4) if len(wins) else None,
        "mean_loss_pct": round(float(np.mean(losses)), 4) if len(losses) else None,
        "payoff_ratio": (
            round(float(np.mean(wins) / -np.mean(losses)), 4)
            if len(wins) and len(losses) and np.mean(losses) < 0
            else None
        ),
        "top2_contribution_pp": round(top2, 4),
        "rest_contribution_pp": round(float(np.sum(r) - top2), 4),
        "longest_losing_streak": longest_losing_streak(list(r)),
        "median_holding_days": round(float(np.median(hours)) / 24, 2),
        "mean_holding_days": round(float(np.mean(hours)) / 24, 2),
        "exit_reasons": {
            reason: int(sum(1 for t in trades if t.exit == reason))
            for reason in sorted({t.exit for t in trades})
        },
    }


def power_analysis(
    mean_pct: float, sd_pct: float, trades_per_year: float, t_target: float = T_TARGET
) -> dict:
    """Trades (and years at the observed frequency) needed for a mean trade of
    this size and dispersion to reach `t_target`: n = (t * sd / mean)^2."""
    if not (mean_pct > 0) or not (sd_pct > 0):
        return {
            "t_target": t_target,
            "trades_needed": None,
            "years_needed": None,
            "note": "mean trade is not positive; no sample size reaches the target",
        }
    trades_needed = math.ceil((t_target * sd_pct / mean_pct) ** 2)
    years = trades_needed / trades_per_year if trades_per_year > 0 else None
    return {
        "t_target": t_target,
        "trades_needed": trades_needed,
        "trades_per_year_observed": round(trades_per_year, 3),
        "years_needed": round(years, 1) if years is not None else None,
    }


# --- resampling ---------------------------------------------------------------


def bootstrap_trades(
    returns_pct: list[float], *, resamples: int = DEFAULT_TRADE_RESAMPLES, seed: int = DEFAULT_SEED
) -> dict:
    """Percentile bootstrap of the mean and the sum of per-trade returns."""
    r = np.asarray(returns_pct, dtype=float)
    n = len(r)
    if n < 2:
        return {"resamples": resamples, "seed": seed, "note": "fewer than two trades"}
    rng = np.random.RandomState(seed)
    idx = rng.randint(0, n, size=(resamples, n))
    samples = r[idx]
    means = samples.mean(axis=1)
    sums = samples.sum(axis=1)
    return {
        "resamples": resamples,
        "seed": seed,
        "mean_ci95_pct": [
            round(float(np.percentile(means, 2.5)), 4),
            round(float(np.percentile(means, 97.5)), 4),
        ],
        "sum_ci95_pp": [
            round(float(np.percentile(sums, 2.5)), 4),
            round(float(np.percentile(sums, 97.5)), 4),
        ],
        "p_mean_le_zero": round(float(np.mean(means <= 0)), 4),
    }


def block_bootstrap_sharpe(
    returns: np.ndarray,
    *,
    block_days: int = DEFAULT_BLOCK_DAYS,
    resamples: int = DEFAULT_SHARPE_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Circular block bootstrap of daily returns; 95 % interval of the
    annualised Sharpe ratio. Blocks keep short-range dependence (trends,
    volatility clustering) that an i.i.d. bootstrap would destroy."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 2 * block_days:
        return {
            "resamples": resamples,
            "block_days": block_days,
            "seed": seed,
            "note": "series too short",
        }
    rng = np.random.RandomState(seed)
    blocks = math.ceil(n / block_days)
    starts = rng.randint(0, n, size=(resamples, blocks))
    offsets = np.arange(block_days)
    idx = ((starts[:, :, None] + offsets[None, None, :]) % n).reshape(resamples, -1)[:, :n]
    samples = r[idx]
    sd = samples.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpes = np.where(
            sd > 0, samples.mean(axis=1) / sd * math.sqrt(ANNUALISATION_DAYS), np.nan
        )
    sharpes = sharpes[~np.isnan(sharpes)]
    if len(sharpes) == 0:
        return {
            "resamples": resamples,
            "block_days": block_days,
            "seed": seed,
            "sharpe_ci95": None,
            "p_sharpe_le_zero": None,
            "note": "Sharpe undefined: all resamples have zero volatility",
        }
    return {
        "resamples": resamples,
        "block_days": block_days,
        "seed": seed,
        "sharpe_ci95": [
            round(float(np.percentile(sharpes, 2.5)), 4),
            round(float(np.percentile(sharpes, 97.5)), 4),
        ],
        "p_sharpe_le_zero": round(float(np.mean(sharpes <= 0)), 4),
    }


def holding_days(trades: list[Trade]) -> list[int]:
    """Days per trade rounded to the nearest whole day (at least one), for the
    random-timing benchmark; rounding up would hand the benchmark extra exposure."""
    return [max(1, round(t.hours / 24)) for t in trades]


def random_timing_trial(
    growth: np.ndarray,
    durations: list[int],
    fee: float,
    notional: float,
    rng: np.random.RandomState,
) -> tuple[float, float]:
    """One trial: place `durations` (days) at random, non-overlapping, in
    `growth` (daily gross growth factors of the asset) and return the net
    return (%) and maximum drawdown (%) of the resulting fixed-notional
    equity path, marked daily, with `fee` per side on each trade."""
    days = len(growth)
    durations = list(durations)
    rng.shuffle(durations)
    total = sum(durations)
    if total > days:
        raise ValueError("trades do not fit in the period")
    free = days - total
    # k+1 gaps that sum to `free`: differences of k sorted uniform cut points.
    cuts = np.sort(rng.randint(0, free + 1, size=len(durations)))
    gaps = np.diff(np.concatenate(([0], cuts, [free])))
    equity = np.empty(days + 1)
    equity[0] = notional
    balance = notional
    pos = 0
    day = 0
    for gap, dur in zip(gaps[:-1], durations, strict=True):  # the last gap runs to the end
        equity[day + 1 : day + 1 + gap] = balance
        day += gap
        path = np.cumprod(growth[day : day + dur])
        held_value = notional * (1 - fee) * path  # bought at fee; marked, exit fee unpaid
        equity[day + 1 : day + 1 + dur] = balance - notional + held_value
        balance += notional * ((1 - fee) ** 2 * path[-1] - 1)
        equity[day + dur] = balance  # exit fee paid on the last held day
        day += dur
        pos += 1
    equity[day + 1 :] = balance
    return (balance / notional - 1) * 100, max_drawdown_pct(equity)


def random_timing_benchmark(
    buy_hold_values: list[float],
    trades: list[Trade],
    *,
    fee: float,
    notional: float,
    actual_return_pct: float,
    actual_drawdown_pct: float,
    trials: int = DEFAULT_TIMING_TRIALS,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Exposure-matched random-timing benchmark (see module docstring)."""
    growth = 1 + daily_returns(buy_hold_values)
    durations = holding_days(trades)
    if not durations:
        return {"trials": 0, "note": "no trades"}
    if sum(durations) > len(growth):
        # Rounding to whole days can overshoot a fully invested period: scale
        # down, keep at least one day each, then trim the longest until it fits.
        scale = len(growth) / sum(durations)
        durations = [max(1, int(d * scale)) for d in durations]
        while sum(durations) > len(growth):
            longest = max(range(len(durations)), key=durations.__getitem__)
            if durations[longest] == 1:
                raise ValueError("more trades than days in the period")
            durations[longest] -= 1
    rng = np.random.RandomState(seed)
    results = np.array(
        [random_timing_trial(growth, durations, fee, notional, rng) for _ in range(trials)]
    )
    returns, drawdowns = results[:, 0], results[:, 1]
    return {
        "trials": trials,
        "seed": seed,
        "fee_per_side": fee,
        "trade_count": len(durations),
        "exposure_days": int(sum(durations)),
        "period_days": int(len(growth)),
        "net_return_pct": {
            "mean": round(float(returns.mean()), 4),
            "p5": round(float(np.percentile(returns, 5)), 4),
            "p50": round(float(np.percentile(returns, 50)), 4),
            "p95": round(float(np.percentile(returns, 95)), 4),
        },
        "max_drawdown_pct": {
            "mean": round(float(drawdowns.mean()), 4),
            "p5": round(float(np.percentile(drawdowns, 5)), 4),
            "p50": round(float(np.percentile(drawdowns, 50)), 4),
            "p95": round(float(np.percentile(drawdowns, 95)), 4),
        },
        "actual_net_return_pct": actual_return_pct,
        # Like for like: the trials are marked daily, so the strategy's own
        # daily-mark drawdown is the comparator, not the deeper recorded 4h figure.
        "actual_max_drawdown_pct_daily": round(actual_drawdown_pct, 4),
        # Share of random timings that did at least as well: a one-sided
        # p-value for "the strategy's timing beat random timing at equal exposure".
        "p_return_ge_actual": round(float(np.mean(returns >= actual_return_pct)), 4),
        "p_drawdown_le_actual": round(float(np.mean(drawdowns <= actual_drawdown_pct)), 4),
    }


def constant_exposure_benchmark(
    buy_hold_values: list[float], exposure_fraction: float, *, fee: float, notional: float
) -> dict:
    """Hold `exposure_fraction` of the notional in the asset every day
    (rebalanced daily, rebalancing cost ignored), the rest in cash; one fee
    per side on the exposed part at the start and the end."""
    r = daily_returns(buy_hold_values)
    path = np.cumprod(1 + exposure_fraction * r)
    equity = np.concatenate(([notional], notional * (1 - fee * exposure_fraction) * path))
    final = equity[-1] * (1 - fee * exposure_fraction)
    equity = np.append(equity, final)
    return {
        "exposure_fraction": round(exposure_fraction, 4),
        "fee_per_side": fee,
        "net_return_pct": round((final / notional - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown_pct(equity), 4),
    }


# --- calendar -----------------------------------------------------------------


def yearly_returns(dates: list[str], strategy: list[float], buy_hold: list[float]) -> list[dict]:
    """Return per calendar year (from the previous year's last mark, or the
    first mark for the first year, to the year's last mark)."""
    frame = pd.DataFrame(
        {"date": pd.to_datetime(dates), "strategy": strategy, "buy_hold": buy_hold}
    )
    out = []
    previous: pd.Series | None = None
    for year, group in frame.groupby(frame["date"].dt.year, sort=True):
        base = previous if previous is not None else group.iloc[0]
        last = group.iloc[-1]
        out.append(
            {
                "year": int(year),
                "from": str(base["date"].date()),
                "to": str(last["date"].date()),
                "strategy_pct": round((last["strategy"] / base["strategy"] - 1) * 100, 2),
                "buy_hold_pct": round((last["buy_hold"] / base["buy_hold"] - 1) * 100, 2),
            }
        )
        previous = last
    return out


# --- decision rule --------------------------------------------------------------


def decision_check(runs: dict) -> dict:
    """K1–K4 of H1.md, recomputed from the recorded headline figures."""
    heldout = runs["heldout-base"]
    stress = runs["heldout-stress"]
    validation = runs["validation-base"]
    k2_limit = K2_FACTOR * heldout["buy_hold_max_drawdown_pct"]
    criteria = [
        {
            "id": "K1",
            "description": "held-out net return > 0, base costs",
            "value": heldout["net_return_pct"],
            "threshold": 0.0,
            "margin_pp": round(heldout["net_return_pct"], 4),
            "pass": heldout["net_return_pct"] > 0,
        },
        {
            "id": "K2",
            "description": f"held-out max drawdown <= {K2_FACTOR} x buy-and-hold max drawdown",
            "value": heldout["mtm_max_drawdown_pct"],
            "threshold": round(k2_limit, 4),
            "margin_pp": round(k2_limit - heldout["mtm_max_drawdown_pct"], 4),
            "pass": heldout["mtm_max_drawdown_pct"] <= k2_limit,
        },
        {
            "id": "K3",
            "description": "held-out net return > 0, stress costs",
            "value": stress["net_return_pct"],
            "threshold": 0.0,
            "margin_pp": round(stress["net_return_pct"], 4),
            "pass": stress["net_return_pct"] > 0,
        },
        {
            "id": "K4",
            "description": "validation net return > 0, base costs",
            "value": validation["net_return_pct"],
            "threshold": 0.0,
            "margin_pp": round(validation["net_return_pct"], 4),
            "pass": validation["net_return_pct"] > 0,
        },
    ]
    all_pass = all(c["pass"] for c in criteria)
    return {
        "criteria": criteria,
        "verdict": "GO" if all_pass else "NO-GO",
        "binding": min(criteria, key=lambda c: c["margin_pp"])["id"],
    }


# --- per run -----------------------------------------------------------------


def assess_run(
    key: str,
    run: dict,
    *,
    notional: float,
    seed: int = DEFAULT_SEED,
    trade_resamples: int = DEFAULT_TRADE_RESAMPLES,
    sharpe_resamples: int = DEFAULT_SHARPE_RESAMPLES,
    timing_trials: int = DEFAULT_TIMING_TRIALS,
) -> dict:
    fee = COST_PER_SIDE[key.rsplit("-", 1)[1]]
    trades = parse_trades(run["trades"])
    start = pd.Timestamp(run["start"], tz="UTC")
    end = pd.Timestamp(run["end"], tz="UTC")
    years = (end - start).days / 365.25
    r_s = daily_returns(run["strategy"])
    r_b = daily_returns(run["buy_hold"])
    beta, corr = beta_and_correlation(r_s, r_b)
    exposure = exposure_pct(trades, start, end)
    stats = trade_statistics(trades)

    def risk_block(
        values: list[float], returns: np.ndarray, net: float, dd: float, cagr: float | None
    ) -> dict:
        return {
            "net_return_pct": net,
            "cagr_pct": cagr,
            "max_drawdown_pct": dd,
            "max_drawdown_pct_daily": round(max_drawdown_pct(values), 4),
            "longest_drawdown_days": longest_drawdown_days(values),
            "annualised_volatility_pct": _round(annualised_volatility_pct(returns)),
            "sharpe": _round(sharpe_ratio(returns)),
            "sortino": _round(sortino_ratio(returns)),
            # Period return over drawdown is not comparable across periods of
            # different length; Calmar (CAGR over drawdown) is.
            "return_over_max_drawdown": round(net / dd, 4) if dd else None,
            "calmar": round(cagr / dd, 4) if dd and cagr is not None else None,
        }

    return {
        "period": {
            "start": run["start"],
            "end": run["end"],
            "days": int((end - start).days),
            "years": round(years, 3),
            "fee_per_side": fee,
        },
        "strategy": {
            **risk_block(
                run["strategy"],
                r_s,
                run["net_return_pct"],
                run["mtm_max_drawdown_pct"],
                run["cagr_pct"],
            ),
            "exposure_pct": round(exposure, 4),
            "beta_to_buy_hold": _round(beta),
            "correlation_to_buy_hold": _round(corr),
        },
        "buy_hold": risk_block(
            run["buy_hold"],
            r_b,
            run["buy_hold_net_return_pct"],
            run["buy_hold_max_drawdown_pct"],
            run["buy_hold_cagr_pct"],
        ),
        "trades": stats,
        "power": power_analysis(
            stats.get("mean_pct", 0.0),
            stats.get("sd_pct", 0.0),
            stats["count"] / years if years > 0 else 0.0,
        ),
        "bootstrap_trades": bootstrap_trades(
            [t.return_pct for t in trades], resamples=trade_resamples, seed=seed
        ),
        "bootstrap_sharpe": block_bootstrap_sharpe(r_s, resamples=sharpe_resamples, seed=seed),
        "random_timing": random_timing_benchmark(
            run["buy_hold"],
            trades,
            fee=fee,
            notional=notional,
            actual_return_pct=run["net_return_pct"],
            actual_drawdown_pct=max_drawdown_pct(run["strategy"]),
            trials=timing_trials,
            seed=seed,
        ),
        "constant_exposure": constant_exposure_benchmark(
            run["buy_hold"], exposure / 100, fee=fee, notional=notional
        ),
        "yearly": yearly_returns(run["dates"], run["strategy"], run["buy_hold"]),
    }


def build_statistics(
    curves: dict,
    *,
    source_sha256: str,
    seed: int = DEFAULT_SEED,
    trade_resamples: int = DEFAULT_TRADE_RESAMPLES,
    sharpe_resamples: int = DEFAULT_SHARPE_RESAMPLES,
    timing_trials: int = DEFAULT_TIMING_TRIALS,
) -> dict:
    notional = float(curves["notional_eur"])
    runs = {
        key: assess_run(
            key,
            run,
            notional=notional,
            seed=seed,
            trade_resamples=trade_resamples,
            sharpe_resamples=sharpe_resamples,
            timing_trials=timing_trials,
        )
        for key, run in curves["runs"].items()
    }
    return {
        "source": "equity-curves.json",
        "source_sha256": source_sha256,
        "pair": curves["pair"],
        "timeframe": curves["timeframe"],
        "notional_eur": curves["notional_eur"],
        "method": {
            "daily_marks": (
                "last 4h close per UTC day; simple daily returns; "
                "365-day annualisation; risk-free rate 0"
            ),
            "trade_returns": (
                "Freqtrade profit_ratio per trade, net of fees, percent of the fixed stake"
            ),
            "bootstrap_trades": "percentile bootstrap of the per-trade returns",
            "bootstrap_sharpe": "circular block bootstrap of daily returns",
            "random_timing": (
                "the run's own trade durations (whole days) placed at random, "
                "non-overlapping, in the period; fee per side on each trade; marked daily"
            ),
            "constant_exposure": (
                "the run's average exposure held in the asset every day, rebalanced daily "
                "without rebalancing cost, base fee on the exposed part at start and end "
                "(base fee for every run, like the buy-and-hold comparator)"
            ),
            "drawdown_comparisons": (
                "benchmark drawdowns are on daily marks and are compared with the "
                "strategy's daily-mark drawdown (max_drawdown_pct_daily), not the 4h record"
            ),
            "seed": seed,
        },
        "decision": decision_check(curves["runs"]),
        "runs": runs,
    }


def write_statistics(experiment: Path, out: Path | None = None, **kwargs) -> Path:
    curves_path = experiment / "equity-curves.json"
    raw = curves_path.read_bytes()
    statistics = build_statistics(
        json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest(), **kwargs
    )
    target = out or (experiment / "statistics.json")
    target.write_text(json.dumps(statistics, indent=1, sort_keys=False, allow_nan=False) + "\n")
    print(f"wrote {target}")
    return target
