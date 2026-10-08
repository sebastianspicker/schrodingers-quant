"""Physics-informed null models for the H1 channel-breakout strategy.

A backtest or a forward test gives one number: the return and drawdown the
rules produced on the one price path that happened. A trader's question is
"how surprising is that number if the market had no exploitable structure?"
A null model answers it: it generates many artificial price paths that share
some properties of the real market and none of the others, runs H1's rules on
each (through the exact replay in `breakout.py`), and counts how often the
artificial paths do as well as the real one. This module is pure
numpy/pandas, so it runs without Docker: `simulate` costs about 10 ms a path.

Model interpretation. Surrogates preserve selected constraints, not every
property of the market. Blocks keep within-block order; IAAFT preserves the
empirical marginal and approximately matches the spectrum. Neither guarantees
removal of every predictable pattern or every nonlinear dependency.

All fitted models generate LOG returns. Zero log drift is not a price
martingale: Gaussian increments imply E[P(t)/P(0)] = exp(sigma^2 t / 2).
Student-t log increments have no finite positive exponential moment, so their
exponentiated prices have no finite expectation. GARCH-t and MRW are finite-path
stress models, not proofs of an efficient market or calibrated financial tails.
The reported GO shares are conditional on these models, not statistical
false-positive rates for a demonstrated no-edge null. Fits and surrogate
comparisons on already inspected history are descriptive.

Synthetic paths have no intrabar information of their own. Highs and lows are
built from the body of each candle widened by upper and lower wick ratios
resampled, with replacement, from the real candles (`wick_ratios`), so the
channel rules see realistic highs and lows. The wick is independent of the
return in the fitted models and in IAAFT; block shuffle keeps each candle's
own wicks. Candles open at the previous close (no gaps) and volume is 1.

Forward-protocol calibration (`forward_calibration`) evaluates F3, F4, P1 and
P2 with idealised fills. An F3 breach disqualifies completion even if the path
later recovers. Reach and years-to-completion exclude stopped paths; timing
ignoring F3 and trades/year over the full simulated horizon are explicitly
counterfactual. P1/P2 shares require eligible completion and use all trials as
the denominator. Buy-and-hold includes entry and reserved liquidation fees.
CooldownPeriod, StoplossGuard and ratios-mode MaxDrawdown are replayed.
Stop-exit and MaxDrawdown-lock frequencies describe the full counterfactual
horizon. F1/F2 operational failures are not simulated. No alternative with an
edge is simulated, so test power is not estimated.

First passage (`stop_hit_probability`, `first_passage_check`). H1 has a -20 %
catastrophe stop. For a Brownian log price the chance that the price touches a
barrier within a horizon has a closed form (reflection principle); the module
evaluates it against both discrete 4h closes and a Brownian-bridge
conditional crossing estimator. The latter integrates out crossings between
closes and reports Monte Carlo standard errors; it matches continuous monitoring.
Neither calculation estimates the joint H1 entry/channel-exit/stop problem.

Fitting. GARCH is fitted by maximum likelihood with the module's own
Nelder-Mead simplex (no scipy). The parameters are unconstrained in the
search: omega through a log, alpha + beta < 1 through a logistic, the share of
alpha in the persistence through a second logistic, and nu = 2.1 + exp(x).
The multifractal fit regresses the covariance of ln|r| at lags 1..max_lag on
-ln(lag): the slope is lambda^2, the intercept is lambda^2 * ln(L). The
multifractal simulator draws the Gaussian log-volatility field by circulant
embedding with the FFT; if the embedding has negative eigenvalues (the
covariance lambda^2 ln(L / (|tau| + 1)) is not guaranteed positive definite
once truncated), they are clipped to zero, which raises the field's variance.
The volatility normalization uses the resulting diagonal covariance.

Randomness uses the legacy `numpy.random.RandomState`, whose stream is stable
across numpy versions, so results are reproducible exactly from the seed. Each
model draws from its own generator (seed plus a hash of its key), so adding a
model does not change the others.
"""

import hashlib
import json
import math
import zlib
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from sq.research import breakout
from sq.research.metrics import max_drawdown_pct

WARMUP = 121  # candles before the window, enough for the 120-candle entry channel
STEP = breakout.STEP
STEPS_PER_YEAR = 6 * 365
DEFAULT_BLOCK = 30

# --- path construction ------------------------------------------------------


def log_returns(candles: pd.DataFrame) -> np.ndarray:
    """Close-to-close log returns; the first uses the first candle's open."""
    close = candles["close"].to_numpy(dtype=float)
    previous = np.concatenate([[float(candles["open"].iloc[0])], close[:-1]])
    return np.log(close / previous)


def wick_ratios(candles: pd.DataFrame) -> np.ndarray:
    """Upper and lower wick per candle as ratios of the body, shape (n, 2).

    Upper is high / max(open, close) - 1, lower is 1 - low / min(open, close).
    """
    o = candles["open"].to_numpy(dtype=float)
    h = candles["high"].to_numpy(dtype=float)
    lo = candles["low"].to_numpy(dtype=float)
    c = candles["close"].to_numpy(dtype=float)
    upper = h / np.maximum(o, c) - 1
    lower = 1 - lo / np.minimum(o, c)
    return np.column_stack([upper, lower])


def _assemble(dates, log_ret: np.ndarray, wick_rows: np.ndarray, p0: float) -> pd.DataFrame:
    close = p0 * np.exp(np.cumsum(log_ret))
    open_ = np.concatenate([[p0], close[:-1]])
    high = np.maximum(open_, close) * (1 + wick_rows[:, 0])
    low = np.minimum(open_, close) * (1 - wick_rows[:, 1])
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(len(close)),
        }
    )


def candles_from_returns(
    log_returns: np.ndarray,
    wicks: np.ndarray,
    rng: np.random.RandomState,
    start: pd.Timestamp,
    p0: float,
) -> pd.DataFrame:
    """A 4h OHLCV frame from log returns.

    close = p0 * exp(cumsum), open = previous close (the first open is p0),
    high and low are the body extremes widened by wick ratios drawn at random,
    with replacement, from `wicks` (the real candles' wicks): a synthetic path
    has no intrabar information of its own.
    """
    n = len(log_returns)
    rows = wicks[rng.randint(0, len(wicks), size=n)]
    dates = pd.date_range(start, periods=n, freq=STEP)
    return _assemble(dates, np.asarray(log_returns, dtype=float), rows, p0)


def block_shuffle(
    candles: pd.DataFrame, rng: np.random.RandomState, block: int = DEFAULT_BLOCK
) -> pd.DataFrame:
    """Candles shuffled in non-overlapping blocks of `block` (default 5 days).

    Each candle keeps its return and its wicks; prices are rebuilt from the
    shuffled log returns with the same first open and the same dates. The last
    block may be shorter. Preserves the marginal distribution and the
    clustering within a block; destroys structure beyond the block.
    """
    r = log_returns(candles)
    wicks = wick_ratios(candles)
    n = len(r)
    starts = np.arange(0, n, block)
    order = rng.permutation(len(starts))
    index = np.concatenate([np.arange(starts[k], min(starts[k] + block, n)) for k in order])
    return _assemble(
        candles["date"].to_numpy(), r[index], wicks[index], float(candles["open"].iloc[0])
    )


def iaaft(x: np.ndarray, rng: np.random.RandomState, iterations: int = 200) -> np.ndarray:
    """Iterative Amplitude Adjusted Fourier Transform surrogate (Schreiber and
    Schmitz 1996): the same sorted values exactly, the power spectrum matched
    iteratively. The loop stops early when the ranking no longer changes.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    sorted_x = np.sort(x)
    amplitude = np.abs(np.fft.rfft(x))
    y = rng.permutation(x)
    ranks = np.argsort(np.argsort(y))
    for _ in range(iterations):
        spectrum = np.fft.rfft(y)
        magnitude = np.abs(spectrum)
        phase = np.divide(spectrum, magnitude, out=np.ones_like(spectrum), where=magnitude > 0)
        y = np.fft.irfft(amplitude * phase, n)
        new_ranks = np.argsort(np.argsort(y))
        y = sorted_x[new_ranks]
        if np.array_equal(new_ranks, ranks):
            break
        ranks = new_ranks
    return y


def iaaft_candles(candles: pd.DataFrame, rng: np.random.RandomState) -> pd.DataFrame:
    """OHLCV rebuilt from an IAAFT surrogate of the log returns, wicks resampled."""
    surrogate = iaaft(log_returns(candles), rng)
    rebuilt = candles_from_returns(
        surrogate,
        wick_ratios(candles),
        rng,
        candles["date"].iloc[0],
        float(candles["open"].iloc[0]),
    )
    rebuilt["date"] = candles["date"].to_numpy()
    return rebuilt


# --- optimiser --------------------------------------------------------------


def nelder_mead(
    f: Callable[[np.ndarray], float],
    x0: np.ndarray,
    step: float = 0.1,
    xtol: float = 1e-8,
    ftol: float = 1e-10,
    max_iter: int = 1000,
    restarts: int = 1,
) -> tuple[np.ndarray, float]:
    """Minimise `f` from `x0` with the Nelder-Mead simplex.

    Standard coefficients (reflection 1, expansion 2, contraction and shrink
    1/2). Stops when the simplex is smaller than `xtol` and the spread of its
    values smaller than `ftol`, or after `max_iter` iterations; the search is
    then restarted from the best point `restarts` times with a fresh simplex.
    Returns the best point and its value.
    """
    best = np.asarray(x0, dtype=float)
    best_value = float(f(best))
    n = len(best)
    for _ in range(restarts + 1):
        simplex = np.vstack([best] + [best + step * np.eye(n)[i] for i in range(n)])
        values = np.array([best_value] + [float(f(p)) for p in simplex[1:]])
        for _ in range(max_iter):
            order = np.argsort(values)
            simplex, values = simplex[order], values[order]
            if (
                np.max(np.abs(values - values[0])) <= ftol
                and np.max(np.abs(simplex - simplex[0])) <= xtol
            ):
                break
            centroid = simplex[:-1].mean(axis=0)
            reflected = centroid + (centroid - simplex[-1])
            f_reflected = float(f(reflected))
            if f_reflected < values[0]:
                expanded = centroid + 2 * (centroid - simplex[-1])
                f_expanded = float(f(expanded))
                if f_expanded < f_reflected:
                    simplex[-1], values[-1] = expanded, f_expanded
                else:
                    simplex[-1], values[-1] = reflected, f_reflected
            elif f_reflected < values[-2]:
                simplex[-1], values[-1] = reflected, f_reflected
            else:
                if f_reflected < values[-1]:
                    contracted = centroid + 0.5 * (reflected - centroid)
                else:
                    contracted = centroid + 0.5 * (simplex[-1] - centroid)
                f_contracted = float(f(contracted))
                if f_contracted < min(f_reflected, values[-1]):
                    simplex[-1], values[-1] = contracted, f_contracted
                else:
                    simplex[1:] = simplex[0] + 0.5 * (simplex[1:] - simplex[0])
                    values[1:] = [float(f(p)) for p in simplex[1:]]
        k = int(np.argmin(values))
        best, best_value = simplex[k].copy(), float(values[k])
    return best, best_value


# --- fitted simulators ------------------------------------------------------


def fit_gbm(r: np.ndarray) -> dict:
    """Mean and standard deviation of the 4h log returns."""
    r = np.asarray(r, dtype=float)
    return {"mu": float(r.mean()), "sigma": float(r.std(ddof=1))}


def simulate_gbm(
    params: dict, n: int, rng: np.random.RandomState, drift: bool = False
) -> np.ndarray:
    """n iid normal log returns; mu is used only when `drift` is true."""
    mu = params["mu"] if drift else 0.0
    return mu + params["sigma"] * rng.standard_normal(n)


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x >= 0 else math.exp(x) / (1 + math.exp(x))


def _garch_params(theta: np.ndarray) -> tuple[float, float, float, float, float]:
    """(mu, omega, alpha, beta, nu) from the unconstrained vector."""
    persistence = 0.9999 * _sigmoid(float(theta[2]))
    share = _sigmoid(float(theta[3]))
    nu = 2.1 + math.exp(min(float(theta[4]), 7.0))
    return (
        float(theta[0]),
        math.exp(max(min(float(theta[1]), 5.0), -30.0)),
        persistence * share,
        persistence * (1 - share),
        nu,
    )


def _garch_variance(e2: np.ndarray, omega: float, alpha: float, beta: float, s0: float):
    out = [0.0] * len(e2)
    s = s0
    for t, value in enumerate(e2.tolist()):
        out[t] = s
        s = omega + alpha * value + beta * s
    return np.array(out)


def _garch_nll(theta: np.ndarray, r: np.ndarray) -> float:
    mu, omega, alpha, beta, nu = _garch_params(theta)
    e = r - mu
    sig2 = _garch_variance(e * e, omega, alpha, beta, float(r.var()))
    if not np.all(np.isfinite(sig2)) or sig2.min() <= 0:
        return 1e10
    loglik = (
        math.lgamma((nu + 1) / 2)
        - math.lgamma(nu / 2)
        - 0.5 * math.log(math.pi * (nu - 2))
        - 0.5 * np.log(sig2)
        - (nu + 1) / 2 * np.log1p(e * e / (sig2 * (nu - 2)))
    )
    return float(-loglik.mean())


def fit_garch_t(r: np.ndarray) -> dict:
    """GARCH(1,1) with standardised Student-t innovations by maximum likelihood.

    Returns mu, omega, alpha, beta, nu, the total log-likelihood and the
    persistence alpha + beta. The search runs on returns scaled to unit
    standard deviation and the result is scaled back.
    """
    r = np.asarray(r, dtype=float)
    scale = float(r.std())
    z = r / scale
    theta0 = np.array([float(z.mean()), math.log(0.05), math.log(0.95 / 0.05), -2.0, math.log(4)])
    theta, nll = nelder_mead(
        lambda t: _garch_nll(t, z), theta0, step=0.3, xtol=1e-5, ftol=1e-9, max_iter=500
    )
    mu, omega, alpha, beta, nu = _garch_params(theta)
    return {
        "mu": mu * scale,
        "mean": float(r.mean()),
        "omega": omega * scale**2,
        "alpha": alpha,
        "beta": beta,
        "nu": nu,
        "loglik": float(-nll * len(z) - len(z) * math.log(scale)),
        "persistence": alpha + beta,
    }


def simulate_garch_t(
    params: dict, n: int, rng: np.random.RandomState, drift: bool = False, burn_in: int = 500
) -> np.ndarray:
    """n log returns of the fitted GARCH(1,1)-t after `burn_in` discarded steps.

    With `drift` false mu is set to 0.
    """
    nu = params["nu"]
    total = n + burn_in
    z = rng.standard_t(nu, size=total) / math.sqrt(nu / (nu - 2))
    omega, alpha, beta = params["omega"], params["alpha"], params["beta"]
    coefficient = (alpha * z * z + beta).tolist()
    variance = [0.0] * total
    s = omega / max(1 - alpha - beta, 1e-6)
    for t in range(total):
        variance[t] = s
        s = omega + coefficient[t] * s
    # The drifted variant uses the window's sample mean, not the Student-t
    # location `mu`, which on heavy-tailed data sits well away from the mean.
    mu = params.get("mean", params["mu"]) if drift else 0.0
    return (mu + np.sqrt(np.array(variance)) * z)[burn_in:]


def fit_mrw(r: np.ndarray, max_lag: int = 120) -> dict:
    """Multifractal random walk parameters (Bacry, Delour and Muzy 2001).

    sigma is the standard deviation of r. lambda^2 is the slope of
    Cov(ln|r_t|, ln|r_{t+tau}|) against -ln(tau) for tau = 1..max_lag (zero
    |r| replaced by the smallest positive one); the intercept is
    lambda^2 ln(L), giving the integral scale L, clipped to [max_lag, 10 n].
    A non-positive slope gives lambda^2 = 0 (no multifractality).
    """
    r = np.asarray(r, dtype=float)
    n = len(r)
    absolute = np.abs(r)
    positive = absolute[absolute > 0]
    log_abs = np.log(np.where(absolute > 0, absolute, positive.min()))
    centred = log_abs - log_abs.mean()
    lags = np.arange(1, max_lag + 1)
    cov = np.array([np.mean(centred[:-tau] * centred[tau:]) for tau in lags])
    slope, intercept = np.polyfit(-np.log(lags), cov, 1)
    if slope <= 1e-12:
        lambda2, integral = 0.0, float(max_lag)
    else:
        lambda2 = float(slope)
        integral = float(np.clip(math.exp(min(intercept / slope, 50.0)), max_lag, 10 * n))
    return {"sigma": float(r.std(ddof=1)), "lambda2": lambda2, "integral_scale": integral}


def simulate_mrw(
    params: dict, n: int, rng: np.random.RandomState, drift: bool = False
) -> np.ndarray:
    """n log returns of a multifractal random walk.

    omega is a stationary Gaussian process with covariance
    lambda^2 ln(L / (|tau| + 1)) for |tau| < L and 0 beyond, drawn by circulant
    embedding with the FFT (negative eigenvalues of the embedding are clipped
    to zero). Then r = sigma * eps * exp(omega - Var(omega)) with eps standard
    normal, so that Var(r) is sigma^2 using the actual embedding variance after clipping.
    `drift` adds params["mu"] if present; the fit does not estimate it.
    """
    lambda2, integral = params["lambda2"], params["integral_scale"]
    eps = rng.standard_normal(n)
    mu = params.get("mu", 0.0) if drift else 0.0
    if lambda2 <= 0:
        return mu + params["sigma"] * eps
    size = 1 << (max(2 * n, 4) - 1).bit_length()
    lags = np.arange(size // 2 + 1)
    gamma = np.where(lags < integral, lambda2 * np.log(integral / (lags + 1.0)), 0.0)
    gamma = np.clip(gamma, 0.0, None)
    row = np.concatenate([gamma, gamma[-2:0:-1]])
    eigenvalues = np.clip(np.fft.fft(row).real, 0.0, None)
    noise = rng.standard_normal(size) + 1j * rng.standard_normal(size)
    field = (np.fft.fft(np.sqrt(eigenvalues) * noise) / math.sqrt(size)).real[:n]
    variance = float(eigenvalues.mean())
    return mu + params["sigma"] * eps * np.exp(field - variance)


MODELS: dict[str, dict] = {
    "gbm_zero_drift": {
        "label": "Geometric Brownian motion, zero log drift",
        "fit": fit_gbm,
        "simulate": lambda params, n, rng: simulate_gbm(params, n, rng, drift=False),
    },
    "garch_t_zero_drift": {
        "label": "GARCH(1,1)-t, zero log drift",
        "fit": fit_garch_t,
        "simulate": lambda params, n, rng: simulate_garch_t(params, n, rng, drift=False),
    },
    "garch_t_drift": {
        "label": "GARCH(1,1)-t, with the window's mean log drift",
        "fit": fit_garch_t,
        "simulate": lambda params, n, rng: simulate_garch_t(params, n, rng, drift=True),
    },
    "mrw_zero_drift": {
        "label": "Multifractal random walk, zero log drift",
        "fit": fit_mrw,
        "simulate": lambda params, n, rng: simulate_mrw(params, n, rng, drift=False),
    },
}

# --- helpers ----------------------------------------------------------------


def _rng(seed: int, key: str) -> np.random.RandomState:
    return np.random.RandomState((seed + zlib.crc32(key.encode())) % 2**32)


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, dict):
        # Fitted parameters keep the six significant digits of `_fit_summary`.
        return {k: (v if k == "fit" else _round(v, digits)) for k, v in value.items()}
    if isinstance(value, float | np.floating):
        return round(float(value), digits)
    return value


def _fit_summary(params: dict) -> dict:
    """Fitted parameters to six significant digits (omega is of order 1e-6)."""
    return {k: float(f"{v:.6g}") for k, v in params.items()}


def _prefix_hash(outcomes: list) -> str:
    """SHA-256 of the JSON of per-trial outcome lists, floats rounded to 6 decimals.

    Every model draws from its own generator and each trial only draws what it
    needs, so the first k trials are the same whatever the total trial count.
    """
    rounded = [[None if v is None else round(float(v), 6) for v in row] for row in outcomes]
    return hashlib.sha256(json.dumps(rounded).encode()).hexdigest()


def _percentiles(values) -> dict:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return {"p5": None, "p50": None, "p95": None}
    p5, p50, p95 = np.percentile(values, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95)}


# --- B. null comparison on a recorded window --------------------------------


def null_comparison(
    candles: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    fee: float,
    trials: int,
    seed: int,
    block: int = DEFAULT_BLOCK,
    models: dict | None = None,
    prefix: int = 0,
) -> dict:
    """H1 on the real window against surrogate and fitted-model paths.

    With `prefix` > 0 every null also reports `prefix_sha256`, a fingerprint of
    its first `prefix` trial outcomes (see `_prefix_hash`); it does not depend
    on `trials`.

    Surrogates (block shuffle, IAAFT) resample the window's own candles
    together with the 121 candles before it (the warm-up), so the first signal
    is possible at the same point as in the real run. Fitted models are fitted
    on the window's log returns and simulate the window's length after the
    same number of warm-up candles the real run had (121, or fewer when the
    history starts inside the warm-up, as for the train window). Per null:
    the share of trials with net return at least the observed one, with
    drawdown at most the observed one, with both, the 5th, 50th and 95th
    percentiles of return, drawdown and trade count, and the share of paths
    with a stop exit or a MaxDrawdown protection lock. Protection semantics
    are covered separately by synthetic and pinned-runtime contract tests.
    """
    models = MODELS if models is None else models
    dates = candles["date"]
    idx = np.flatnonzero(((dates >= start) & (dates < end)).to_numpy())
    i0, i1 = int(idx[0]), int(idx[-1])
    window = candles.iloc[i0 : i1 + 1].reset_index(drop=True)
    extended = candles.iloc[max(0, i0 - WARMUP) : i1 + 1].reset_index(drop=True)
    warmup = i0 - max(0, i0 - WARMUP)  # synthetic paths get the same warm-up the real run had
    wicks = wick_ratios(extended)
    r_window = log_returns(window)
    p0 = float(window["open"].iloc[0])
    n = len(window)
    synthetic_end = start + n * STEP

    observed = breakout.summary(breakout.simulate(candles, start, end, fee), candles, start, end)

    def run(path: pd.DataFrame, path_end: pd.Timestamp) -> dict:
        trades = breakout.simulate(path, start, path_end, fee)
        return breakout.summary(trades, path, start, path_end)

    nulls: dict[str, tuple[str, Callable[[np.random.RandomState], dict], dict | None]] = {
        "block_shuffle": (
            "Block shuffle of the real candles",
            lambda rng: run(block_shuffle(extended, rng, block), end),
            None,
        ),
        "iaaft": (
            "IAAFT surrogate of the real returns",
            lambda rng: run(iaaft_candles(extended, rng), end),
            None,
        ),
    }
    for key, model in models.items():
        params = model["fit"](r_window)

        def synthetic(rng, model=model, params=params) -> dict:
            r = model["simulate"](params, warmup + n, rng)
            first = start - warmup * STEP
            return run(candles_from_returns(r, wicks, rng, first, p0), synthetic_end)

        nulls[key] = (model["label"], synthetic, params)

    results = {}
    for key, (label, one_trial, params) in nulls.items():
        rng = _rng(seed, key)
        rows = [one_trial(rng) for _ in range(trials)]
        ret = np.array([row["net_return_pct"] for row in rows])
        dd = np.array([row["mtm_max_drawdown_pct"] for row in rows])
        ge = ret >= observed["net_return_pct"]
        le = dd <= observed["mtm_max_drawdown_pct"]
        results[key] = {
            "label": label,
            "share_return_ge": float(ge.mean()),
            "share_drawdown_le": float(le.mean()),
            "share_both": float((ge & le).mean()),
            "return_pct": _percentiles(ret),
            "drawdown_pct": _percentiles(dd),
            "trades": _percentiles([row["trades"] for row in rows]),
            "share_paths_with_stop_exit": float(np.mean([row["stop_exits"] > 0 for row in rows])),
            "share_paths_with_max_drawdown_lock": float(
                np.mean([row["max_drawdown_locks"] > 0 for row in rows])
            ),
            "fit": _fit_summary(params) if params is not None else None,
        }
        if prefix > 0:
            results[key]["prefix_sha256"] = _prefix_hash(
                [
                    [
                        row["net_return_pct"],
                        row["mtm_max_drawdown_pct"],
                        row["trades"],
                        row["max_drawdown_locks"],
                    ]
                    for row in rows[:prefix]
                ]
            )
    return _round(
        {"observed": observed, "trials": trials, "block_candles": block, "models": results}
    )


# --- C. forward-protocol calibration ----------------------------------------


def judge_window(
    equity: pd.Series,
    hold: pd.Series,
    notional: float,
    f3_limit: float,
    p2_factor: float,
    reached: bool,
) -> dict:
    """The forward-test gates on one window.

    F3 STOP: the marked-to-market drawdown from the initial notional exceeds
    `f3_limit` percent at any point. P1: final equity above the notional. P2:
    that drawdown is at most `p2_factor` times buy-and-hold's drawdown on the
    same window. GO needs the required trades (`reached`), no F3 stop, P1 and P2.
    """
    drawdown = max_drawdown_pct(equity, initial=notional)
    hold_drawdown = max_drawdown_pct(hold, initial=notional)
    f3_stop = drawdown > f3_limit
    p1 = float(equity.iloc[-1]) > notional
    p2 = drawdown <= p2_factor * hold_drawdown
    return {
        "f3_stop": bool(f3_stop),
        "p1_pass": bool(p1),
        "p2_pass": bool(p2),
        "go": bool(reached and not f3_stop and p1 and p2),
        "drawdown_pct": drawdown,
        "hold_drawdown_pct": hold_drawdown,
    }


def forward_calibration(
    r_fit: np.ndarray,
    wicks: np.ndarray,
    trials: int,
    seed: int,
    fee: float = 0.005,
    trades_required: int = 30,
    f3_drawdown_limit_pct: float = 31.31,
    p2_factor: float = 0.6,
    max_years: int = 10,
    models: tuple = tuple(MODELS),
    prefix: int = 0,
) -> dict:
    """The forward-test protocol (F3, F4, P1, P2) on simulated futures.

    With `prefix` > 0 every model also reports `prefix_sha256` of its first
    `prefix` trials' [eligible completion, counterfactual years or null,
    f3, raw p1, raw p2, full-horizon MaxDrawdown lock count]; independent of
    `trials`. Public P1/P2 shares additionally require eligible completion.

    Each model is fitted on `r_fit`, `max_years` of 4h candles (plus warm-up)
    are simulated per trial and H1 is run once per path. The window ends at the
    close of the `trades_required`-th completed trade, or at the path end if
    there are fewer (then the path did not reach F4). Shares are over all
    trials. `years_to_required_trades` uses only paths reaching the target
    without an F3 breach. `*_ignoring_f3` and `trades_per_year` describe
    counterfactual continuation despite STOP, the latter over `max_years`.
    """
    n = STEPS_PER_YEAR * max_years
    start = pd.Timestamp("2030-01-01", tz="UTC")
    first = start - WARMUP * STEP
    path_end = start + n * STEP
    p0 = 1000.0
    notional = breakout.NOTIONAL
    fits: dict[str, dict] = {}
    results = {}
    for key in models:
        model = MODELS[key]
        fit_name = model["fit"].__name__
        if fit_name not in fits:
            fits[fit_name] = model["fit"](r_fit)
        params = fits[fit_name]
        rng = _rng(seed, key)
        years, unconstrained_years, per_year, outcomes = [], [], [], []
        counts = {"reached": 0, "f3": 0, "p1": 0, "p2": 0, "go": 0, "stop": 0, "drawdown_lock": 0}
        for _ in range(trials):
            r = model["simulate"](params, WARMUP + n, rng)
            path = candles_from_returns(r, wicks, rng, first, p0)
            trades = breakout.simulate(path, start, path_end, fee)
            completed = trades[trades["exit_reason"] != "force_exit"]
            per_year.append(len(completed) / max_years)
            counts["stop"] += int((trades["exit_reason"] == "stop_loss").any())
            lock_count = int(trades.attrs.get("max_drawdown_locks", 0))
            counts["drawdown_lock"] += lock_count > 0
            reached = len(completed) >= trades_required
            trial_years = None
            if reached:
                used = completed.iloc[:trades_required]
                window_end = used["close_date"].iloc[-1] + STEP
                trial_years = (used["close_date"].iloc[-1] - start) / pd.Timedelta(days=365)
                unconstrained_years.append(trial_years)
            else:
                used, window_end = trades, path_end
            curve = breakout.equity(used, path, start, window_end, notional)
            marks = path.set_index("date").loc[curve.index]
            # Buy-and-hold as sq.live.forward.benchmark_report: bought at the
            # window's first open, entry fee inside the budget, exit fee reserved.
            hold = notional / (1 + fee) / float(marks["open"].iloc[0]) * marks["close"] * (1 - fee)
            verdict = judge_window(curve, hold, notional, f3_drawdown_limit_pct, p2_factor, reached)
            # F3 is latched: later trades cannot complete the protocol after STOP.
            # Retain unconstrained timing separately to make this censoring visible.
            reached = reached and not verdict["f3_stop"]
            if reached:
                years.append(trial_years)
            counts["reached"] += reached
            counts["f3"] += verdict["f3_stop"]
            counts["p1"] += reached and verdict["p1_pass"]
            counts["p2"] += reached and verdict["p2_pass"]
            counts["go"] += verdict["go"]
            outcomes.append(
                [
                    int(reached),
                    trial_years,
                    int(verdict["f3_stop"]),
                    int(verdict["p1_pass"]),
                    int(verdict["p2_pass"]),
                    lock_count,
                ]
            )
        results[key] = {
            "label": model["label"],
            "trials": trials,
            "share_reached_required_trades": counts["reached"] / trials,
            "years_to_required_trades": _percentiles(years),
            "share_reached_ignoring_f3": len(unconstrained_years) / trials,
            "years_to_required_trades_ignoring_f3": _percentiles(unconstrained_years),
            "trades_per_year": _percentiles(per_year),
            "share_f3_stop": counts["f3"] / trials,
            "share_p1_pass": counts["p1"] / trials,
            "share_p2_pass": counts["p2"] / trials,
            "share_go": counts["go"] / trials,
            "share_paths_with_stop_exit": counts["stop"] / trials,
            "share_paths_with_max_drawdown_lock": counts["drawdown_lock"] / trials,
            "fit": _fit_summary(params),
        }
        if prefix > 0:
            results[key]["prefix_sha256"] = _prefix_hash(outcomes[:prefix])
    return _round(
        {
            "protocol": {
                "fee": fee,
                "trades_required": trades_required,
                "f3_drawdown_limit_pct": f3_drawdown_limit_pct,
                "p2_factor": p2_factor,
                "max_years": max_years,
                "trials": trials,
                "seed": seed,
            },
            "models": results,
        }
    )


# --- D. first passage -------------------------------------------------------


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def stop_hit_probability(
    sigma_annual: float, horizon_days: float, stop: float = 0.20, drift_annual: float = 0.0
) -> float:
    """Probability that a Brownian log price with drift `drift_annual` and
    volatility `sigma_annual` (both per year) touches ln(1 - stop) within the
    horizon (reflection principle, continuous monitoring).
    """
    b = -math.log(1 - stop)
    t = horizon_days / 365
    s = sigma_annual * math.sqrt(t)
    mu = drift_annual
    return _phi((-b - mu * t) / s) + math.exp(-2 * mu * b / sigma_annual**2) * _phi(
        (-b + mu * t) / s
    )


def first_passage_check(
    sigma_annual: float,
    horizons_days: tuple = (7, 14, 30, 60),
    trials: int = 2000,
    seed: int = 0,
    stop: float = 0.2,
) -> dict:
    """The analytic stop-hit probability against simulated paths.

    Simulated paths are zero-drift Brownian log prices in 4h steps; a path hits
    when its running minimum of the 4h close falls below p0 * (1 - stop)
    within the horizon. `bridge_continuous` instead integrates the conditional
    crossing probability between endpoints, with its Monte Carlo standard error.
    """
    rng = _rng(seed, "first_passage")
    steps = [int(round(days * 6)) for days in horizons_days]
    sigma_step = sigma_annual / math.sqrt(STEPS_PER_YEAR)
    log_price = np.cumsum(sigma_step * rng.standard_normal((trials, max(steps))), axis=1)
    running_min = np.minimum.accumulate(log_price, axis=1)
    barrier = math.log(1 - stop)
    # Conditional crossing probability of each Brownian bridge between closes.
    # Given endpoints x,y above b: P(min <= b) = exp(-2(x-b)(y-b)/sigma_step^2).
    previous = np.concatenate((np.zeros((trials, 1)), log_price[:, :-1]), axis=1)
    above = (previous > barrier) & (log_price > barrier)
    distance_product = np.maximum((previous - barrier) * (log_price - barrier), 0)
    crossing = np.where(above, np.exp(-2 * distance_product / sigma_step**2), 1.0)
    survival = np.cumprod(1 - crossing, axis=1)
    analytic, simulated, bridge, bridge_se = {}, {}, {}, {}
    for days, k in zip(horizons_days, steps, strict=True):
        analytic[str(days)] = stop_hit_probability(sigma_annual, days, stop)
        simulated[str(days)] = float((running_min[:, k - 1] <= barrier).mean())
        hit = 1 - survival[:, k - 1]
        bridge[str(days)] = float(hit.mean())
        bridge_se[str(days)] = float(hit.std(ddof=1) / math.sqrt(trials))
    return _round(
        {
            "sigma_annualised_pct": sigma_annual * 100,
            "stop_pct": stop * 100,
            "analytic": analytic,
            "simulated": simulated,
            "bridge_continuous": bridge,
            "bridge_standard_error": bridge_se,
            "trials": trials,
        }
    )
