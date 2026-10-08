"""Stylized facts of BTC/EUR 4h returns: the physics-style diagnostics, for the record.

Pure numpy/pandas, no I/O, so it runs without the Freqtrade image. Each figure
below answers one question a trader should ask before trusting a rule such as
the H1 channel breakout, and each is reported per period (train, validation,
held-out and the full sample) so a regime change is visible.

- Annualised volatility, skewness, excess kurtosis. Volatility sets the scale
  of every stop and fee comparison; skewness says whether big moves are
  mostly down or up; excess kurtosis (0 for a Gaussian) says how much more
  often than a bell curve the market makes extreme moves.
- Tail index (Hill estimator) of losses and of gains. The fraction of moves
  larger than x falls like x^-alpha. For a Gaussian alpha is large (thin
  tails); equity returns typically give alpha near 3, the "inverse cubic law"
  (Gopikrishnan et al. 1998). A small alpha means that a few days decide the
  result and that sample means and Sharpe ratios are unreliable.
- Autocorrelation of returns and of absolute returns at a few lags (in 4h
  candles). Returns are close to uncorrelated (no linear edge from yesterday's
  move); absolute returns are strongly correlated (volatility clusters).
- Hurst exponent H by detrended fluctuation analysis (Peng et al. 1994).
  H near 0.5 is consistent with iid increments at the fitted scales; it
  does not rule out drift or nonlinear predictability. Shuffle ranges test
  this scaling diagnostic, not whether a breakout can earn money.
  H of absolute returns describes volatility scaling over those scales.
- Permutation entropy (Bandt and Pompe 2002) of returns, scaled to [0, 1].
  One means uniform ordinal-pattern frequencies at the chosen order and lag.
  It does not imply independence or exclude other forms of predictability.
- Multifractal random walk intermittency lambda^2 (Bacry, Delour, Muzy 2001).
  The covariance of ln|r| at lag tau falls like lambda^2 ln(L / tau); lambda^2
  is how strongly volatility bursts cascade across time scales (0 for iid
  returns) and L is the time scale (in candles) over which they stay related.

Randomness uses the legacy `numpy.random.RandomState`, whose stream is
guaranteed stable across numpy versions, so the record is reproducible.
"""

import math

import numpy as np
import pandas as pd

DEFAULT_SEED = 20260924  # H1's predeclaration date
DEFAULT_RESAMPLES = 200
CANDLES_PER_YEAR = 6 * 365
DEFAULT_LAGS = (1, 2, 6, 30)


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round for the record; NaN or infinity (undefined figure) becomes None."""
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def log_returns(close: np.ndarray) -> np.ndarray:
    """Log returns of a close series; one element shorter than the input."""
    return np.diff(np.log(np.asarray(close, dtype=float)))


def moments(r: np.ndarray) -> dict[str, float]:
    """Mean, standard deviation, skewness, excess kurtosis, annualised volatility (%)."""
    r = np.asarray(r, dtype=float)
    mean = float(r.mean())
    sd = float(r.std())
    z = (r - mean) / sd if sd > 0 else np.zeros_like(r)
    return {
        "mean": mean,
        "sd": sd,
        "skewness": float(np.mean(z**3)),
        "excess_kurtosis": float(np.mean(z**4) - 3.0),
        "annualised_volatility_pct": sd * math.sqrt(CANDLES_PER_YEAR) * 100.0,
    }


def _hill(positive_sorted_desc: np.ndarray, k: int) -> float:
    """Hill estimator from descending order statistics: k exceedances over the (k+1)-th."""
    threshold = positive_sorted_desc[k]
    return float(1.0 / np.mean(np.log(positive_sorted_desc[:k] / threshold)))


def hill_tail_index(r: np.ndarray, k: int | None = None) -> dict[str, float | int]:
    """Hill tail index alpha of the k largest losses (`left`) and gains (`right`).

    Default k is max(20, 5 % of n). alpha near 3 is the "inverse cubic law"
    (Gopikrishnan et al. 1998); a Gaussian gives a large alpha.
    """
    r = np.asarray(r, dtype=float)
    if k is None:
        k = max(20, int(0.05 * len(r)))
    losses = np.sort(-r[r < 0])[::-1]
    gains = np.sort(r[r > 0])[::-1]
    if k >= min(len(losses), len(gains)):
        raise ValueError("not enough returns for the requested number of tail observations")
    return {"left": _hill(losses, k), "right": _hill(gains, k), "k": int(k)}


def acf(x: np.ndarray, lags: list[int] | tuple[int, ...]) -> dict[str, float]:
    """Sample autocorrelation at the given lags (biased denominator), keyed by lag."""
    x = np.asarray(x, dtype=float)
    d = x - x.mean()
    denom = float(np.dot(d, d))
    return {
        str(lag): float(np.dot(d[: len(d) - lag], d[lag:]) / denom) if denom > 0 else 0.0
        for lag in lags
    }


def _default_scales(n: int) -> np.ndarray:
    return np.unique(np.round(np.geomspace(16, n // 4, 12)).astype(int))


def _box_fluctuation(profile: np.ndarray, s: int) -> float:
    """Mean squared residual of a linear fit in non-overlapping boxes, from both ends."""
    t = np.arange(s) - (s - 1) / 2.0
    tt = float(np.dot(t, t))
    total = 0.0
    count = 0
    for series in (profile, profile[::-1]):
        nb = len(series) // s
        boxes = series[: nb * s].reshape(nb, s)
        centred = boxes - boxes.mean(axis=1, keepdims=True)
        slope = centred @ t / tt
        resid = centred - slope[:, None] * t[None, :]
        total += float(np.sum(resid**2))
        count += nb * s
    return total / count


def dfa(x: np.ndarray, scales: np.ndarray | None = None) -> tuple[float, dict]:
    """Hurst exponent by DFA-1 (Peng et al. 1994).

    The profile (cumulative sum of x minus its mean) is cut into boxes of size
    s, from the start and from the end so all data is used; each box is
    detrended with a straight line and F(s) is the RMS residual. H is the
    least-squares slope of log F(s) on log s.
    """
    x = np.asarray(x, dtype=float)
    scales = _default_scales(len(x)) if scales is None else np.asarray(scales, dtype=int)
    profile = np.cumsum(x - x.mean())
    fluct = np.array([math.sqrt(_box_fluctuation(profile, int(s))) for s in scales])
    h = float(np.polyfit(np.log(scales), np.log(fluct), 1)[0])
    return h, {"scales": scales.tolist(), "fluctuations": fluct.tolist()}


def hurst_report(
    r: np.ndarray, resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED
) -> dict:
    """H of returns and of |returns|, with the iid null range for H of returns.

    `shuffle_range95` is the 2.5th and 97.5th percentile of H over random
    permutations of the same returns. H near 0.5 is consistent with iid
    scaling over these scales, not a test excluding all predictability.
    """
    r = np.asarray(r, dtype=float)
    scales = _default_scales(len(r))
    h_ret, _ = dfa(r, scales)
    h_abs, _ = dfa(np.abs(r), scales)
    rng = np.random.RandomState(seed)
    null = np.array([dfa(rng.permutation(r), scales)[0] for _ in range(resamples)])
    low, high = np.percentile(null, [2.5, 97.5])
    return {
        "returns": h_ret,
        "abs_returns": h_abs,
        "shuffle_range95": [float(low), float(high)],
        "scales": [int(scales.min()), int(scales.max())],
    }


def permutation_entropy(x: np.ndarray, order: int = 4, delay: int = 1) -> float:
    """Bandt-Pompe permutation entropy, normalised by log(order!) to [0, 1].

    Ties are ordered by position (stable argsort).
    """
    x = np.asarray(x, dtype=float)
    span = (order - 1) * delay
    windows = np.lib.stride_tricks.sliding_window_view(x, span + 1)[:, ::delay]
    ranks = np.argsort(windows, axis=1, kind="stable")
    codes = ranks @ (order ** np.arange(order))
    counts = np.unique(codes, return_counts=True)[1]
    p = counts / counts.sum()
    return float(-np.sum(p * np.log(p)) / math.log(math.factorial(order)))


def permutation_entropy_report(
    r: np.ndarray, order: int = 4, resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED
) -> dict:
    """Permutation entropy of r and the range chance produces (95 % of shuffles)."""
    r = np.asarray(r, dtype=float)
    rng = np.random.RandomState(seed)
    null = np.array([permutation_entropy(rng.permutation(r), order) for _ in range(resamples)])
    low, high = np.percentile(null, [2.5, 97.5])
    return {
        "value": permutation_entropy(r, order),
        "shuffle_range95": [float(low), float(high)],
        "order": order,
    }


def mrw_intermittency(r: np.ndarray, max_lag: int = 120) -> dict[str, float | None]:
    """Multifractal-random-walk intermittency lambda^2 (Bacry, Delour, Muzy 2001).

    Least-squares fit of Cov(ln|r_t|, ln|r_{t+tau}|) on -ln tau for tau =
    1..max_lag; the slope is lambda^2 and exp(intercept / lambda^2) the
    integral scale L in candles (clipped to [max_lag, 10 n]; None if
    lambda^2 <= 0). Zero returns are replaced by the smallest positive |r|.
    """
    a = np.abs(np.asarray(r, dtype=float))
    a = np.where(a > 0, a, a[a > 0].min())
    y = np.log(a)
    d = y - y.mean()
    lags = np.arange(1, max_lag + 1)
    cov = np.array([np.dot(d[: len(d) - t], d[t:]) / (len(d) - t) for t in lags])
    slope, intercept = np.polyfit(-np.log(lags), cov, 1)
    scale = None
    if slope > 0:
        scale = float(np.clip(math.exp(min(intercept / slope, 700.0)), max_lag, 10 * len(r)))
    return {"lambda2": float(slope), "integral_scale_candles": scale}


def stylized_facts(
    candles: pd.DataFrame,
    periods: dict[str, tuple[str, str]],
    seed: int = DEFAULT_SEED,
    lags: tuple[int, ...] | list[int] = DEFAULT_LAGS,
) -> dict:
    """All diagnostics per period (start inclusive, end exclusive) plus "full"."""
    dates = pd.to_datetime(candles["date"], utc=True)
    spans = dict(periods)
    spans["full"] = (str(dates.min()), str(dates.max() + pd.Timedelta("4h")))
    out = {}
    for name, (start, end) in spans.items():
        mask = (dates >= pd.Timestamp(start, tz="UTC")) & (dates < pd.Timestamp(end, tz="UTC"))
        sub = candles.loc[mask.to_numpy(), "close"].to_numpy(dtype=float)
        r = log_returns(sub)
        mom = moments(r)
        hill = hill_tail_index(r)
        hurst = hurst_report(r, seed=seed)
        pe = permutation_entropy_report(r, seed=seed)
        mrw = mrw_intermittency(r)
        out[name] = {
            "start": start,
            "end": end,
            "candles": len(sub),
            "annualised_volatility_pct": _round(mom["annualised_volatility_pct"]),
            "skewness": _round(mom["skewness"]),
            "excess_kurtosis": _round(mom["excess_kurtosis"]),
            "tail_index": {
                "left": _round(hill["left"]),
                "right": _round(hill["right"]),
                "k": hill["k"],
            },
            "acf_returns": {k: _round(v) for k, v in acf(r, list(lags)).items()},
            "acf_abs_returns": {k: _round(v) for k, v in acf(np.abs(r), list(lags)).items()},
            "hurst": {
                "returns": _round(hurst["returns"]),
                "abs_returns": _round(hurst["abs_returns"]),
                "shuffle_range95": [_round(v) for v in hurst["shuffle_range95"]],
                "scales": hurst["scales"],
            },
            "permutation_entropy": {
                "value": _round(pe["value"]),
                "shuffle_range95": [_round(v) for v in pe["shuffle_range95"]],
                "order": pe["order"],
            },
            "mrw_lambda2": _round(mrw["lambda2"]),
            "mrw_integral_scale_candles": _round(mrw["integral_scale_candles"]),
        }
    return out
