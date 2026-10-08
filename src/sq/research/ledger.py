"""Research-trials ledger and the deflated Sharpe ratio.

The measurement side of the project: every look at the data is a trial. If you
try N unrelated variants and report the best Sharpe ratio, that figure is
inflated by selection alone. The ledger (`research/ledger.json`) records each
look, and the deflated Sharpe ratio (Bailey and Lopez de Prado, 2014) charges
the observed Sharpe ratio for the number of trials.

Ledger shape (schema_version 1):

    {"schema_version": 1, "as_of": "YYYY-MM-DD",
     "entries": [{"id": str, "date": "YYYY-MM-DD",
                  "kind": "hypothesis" | "sensitivity" | "robustness" | "rerun"
                          | "cost_variant" | "benchmark",
                  "description": str, "period": str,
                  "sharpe_annualised": float | null, "counted": bool}]}

`counted` marks the entries that count as independent trials for the
deflation; the rest (benchmarks, pure reruns) are listed but not charged.

Two figures come out of `ledger_report`:

- The expected maximum Sharpe ratio: what the best of N unrelated trials would
  show by luck alone, given how much the trials' Sharpe ratios scatter.
- The deflated Sharpe probability: the chance that the observed Sharpe ratio
  exceeds that luck benchmark, after adjusting for skewness and fat tails of
  the daily returns.

Sharpe ratios inside the formulas are per observation (daily), not annualised.
This is a descriptive charge for selection, not a proof of an edge. Pure
standard library plus numpy: the normal quantile is a bisection on `math.erf`.
"""

import json
import math
from datetime import date
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1
KINDS = frozenset({"hypothesis", "sensitivity", "robustness", "rerun", "cost_variant", "benchmark"})
ANNUALISATION_DAYS = 365
EULER_GAMMA = 0.5772156649015329

NOTE = (
    "The expected maximum Sharpe ratio is what the best of N unrelated trials would show "
    "by luck alone; the deflated Sharpe probability is the chance that the observed Sharpe "
    "ratio exceeds it after adjusting for skewness and fat tails of the daily returns. "
    "It is a charge for selection, not a proof of an edge."
)


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(float(value), digits)


# --- normal distribution ----------------------------------------------------------


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF by bisection on `math.erf` (error below 1e-10)."""
    if not 0 < p < 1:
        raise ValueError("p must be strictly between 0 and 1")
    if p < 0.5:
        return -norm_ppf(1 - p)
    low, high = 0.0, 40.0
    for _ in range(100):
        mid = (low + high) / 2
        if norm_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


# --- the formulas ------------------------------------------------------------------


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum of `n_trials` unrelated Sharpe ratios with the given
    cross-trial variance, in per-observation units (Bailey and Lopez de Prado,
    2014): sqrt(V) ((1 - g) Phi^-1(1 - 1/N) + g Phi^-1(1 - 1/(N e))), with g the
    Euler-Mascheroni constant. One trial gives 0."""
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if sharpe_variance < 0 or not math.isfinite(sharpe_variance):
        raise ValueError("sharpe_variance must be finite and non-negative")
    if n_trials == 1:
        return 0.0
    g = EULER_GAMMA
    return math.sqrt(sharpe_variance) * (
        (1 - g) * norm_ppf(1 - 1 / n_trials) + g * norm_ppf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(
    sharpe: float,
    benchmark_sharpe: float,
    n_obs: int,
    skewness: float,
    excess_kurtosis: float,
) -> float:
    """Probability that the true Sharpe ratio exceeds `benchmark_sharpe`:
    Phi((SR - SR0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1) / 4 SR^2)), with g4 the
    full kurtosis (excess + 3). Per-observation (daily) Sharpe units."""
    if n_obs < 2:
        raise ValueError("n_obs must be at least 2")
    kurtosis = excess_kurtosis + 3
    variance = 1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if variance <= 0:
        raise ValueError("Sharpe variance estimate is not positive for these moments")
    return norm_cdf((sharpe - benchmark_sharpe) * math.sqrt(n_obs - 1) / math.sqrt(variance))


# --- ledger file -------------------------------------------------------------------


def _check_date(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name}: expected an ISO date string")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name}: not an ISO date (YYYY-MM-DD): {value!r}") from None


def load_ledger(path: Path | str) -> dict:
    """Read and validate a ledger file; raise ValueError with the reason if invalid."""
    ledger = json.loads(Path(path).read_text())
    if not isinstance(ledger, dict):
        raise ValueError("ledger: expected an object")
    if ledger.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"ledger: schema_version must be {SCHEMA_VERSION}")
    for key in ("as_of", "entries"):
        if key not in ledger:
            raise ValueError(f"ledger: missing key {key!r}")
    _check_date(ledger["as_of"], "ledger.as_of")
    if not isinstance(ledger["entries"], list):
        raise ValueError("ledger.entries: expected a list")
    seen: set[str] = set()
    for i, entry in enumerate(ledger["entries"]):
        name = f"ledger.entries[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: expected an object")
        for key in ("id", "date", "kind", "description", "period", "sharpe_annualised", "counted"):
            if key not in entry:
                raise ValueError(f"{name}: missing key {key!r}")
        for key in ("id", "description", "period"):
            if not isinstance(entry[key], str) or not entry[key]:
                raise ValueError(f"{name}.{key}: expected a non-empty string")
        if entry["id"] in seen:
            raise ValueError(f"{name}.id: duplicate id {entry['id']!r}")
        seen.add(entry["id"])
        _check_date(entry["date"], f"{name}.date")
        if entry["kind"] not in KINDS:
            raise ValueError(f"{name}.kind: {entry['kind']!r} not in {sorted(KINDS)}")
        if not isinstance(entry["counted"], bool):
            raise ValueError(f"{name}.counted: expected true or false")
        sharpe = entry["sharpe_annualised"]
        if sharpe is not None and (
            isinstance(sharpe, bool)
            or not isinstance(sharpe, (int, float))
            or not math.isfinite(sharpe)
        ):
            raise ValueError(f"{name}.sharpe_annualised: expected a finite number or null")
    return ledger


# --- report ------------------------------------------------------------------------


def ledger_report(
    ledger: dict, daily_returns: np.ndarray, annualisation_days: int = ANNUALISATION_DAYS
) -> dict:
    """Deflated Sharpe ratio of the observed daily returns, charged for the
    ledger's counted trials. Skewness and excess kurtosis are the population
    moments (ddof 0) of the daily returns. Daily Sharpe quantities are rounded
    to 6 digits (they are small), the daily variance to 8, the rest to 4."""
    entries = ledger["entries"]
    root = math.sqrt(annualisation_days)
    counted = [e for e in entries if e["counted"]]
    r = np.asarray(daily_returns, dtype=float)
    n_obs = len(r)

    sd = float(np.std(r, ddof=1)) if n_obs > 1 else 0.0
    sharpe_daily = float(np.mean(r)) / sd if sd > 0 else None
    skewness = excess_kurtosis = None
    if sharpe_daily is not None:
        z = (r - np.mean(r)) / np.std(r)
        skewness = float(np.mean(z**3))
        excess_kurtosis = float(np.mean(z**4)) - 3

    recorded = [
        e["sharpe_annualised"] / root for e in counted if e["sharpe_annualised"] is not None
    ]
    variance = float(np.var(recorded, ddof=1)) if len(recorded) >= 2 else None
    expected_max = (
        expected_max_sharpe(len(counted), variance) if variance is not None and counted else None
    )
    probability = None
    if expected_max is not None and sharpe_daily is not None:
        try:
            probability = deflated_sharpe(
                sharpe_daily, expected_max, n_obs, skewness, excess_kurtosis
            )
        except ValueError:
            probability = None

    return {
        "trials_counted": len(counted),
        "entries": [
            {
                "id": e["id"],
                "kind": e["kind"],
                "counted": e["counted"],
                "sharpe_annualised": e["sharpe_annualised"],
            }
            for e in entries
        ],
        "observed_sharpe_annualised": _round(None if sharpe_daily is None else sharpe_daily * root),
        "observed_sharpe_daily": _round(sharpe_daily, 6),
        "daily_observations": n_obs,
        "skewness": _round(skewness),
        "excess_kurtosis": _round(excess_kurtosis),
        "sharpe_variance_daily": _round(variance, 8),
        "expected_max_sharpe_daily": _round(expected_max, 6),
        "expected_max_sharpe_annualised": _round(
            None if expected_max is None else expected_max * root
        ),
        "deflated_sharpe_probability": _round(probability),
        "note": NOTE,
    }
