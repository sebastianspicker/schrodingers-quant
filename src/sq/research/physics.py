"""Build `physics.json`: the physics-informed assessment of a recorded experiment.

`make physics` writes `research/experiments/H1/physics.json` from the tracked
proxy candles (`research/data/`), the recorded daily curves
(`equity-curves.json`) and the research-trials ledger (`research/ledger.json`).
Everything runs on a workstation without Docker; the Monte Carlo sections take
a few minutes. Sections, and the module behind each:

- `stylized_facts` (`sq.research.stylized`): what the market looks like per
  period: tails, volatility clustering, Hurst exponent, permutation entropy,
  multifractal intermittency.
- `null_models` (`sq.research.nulls`): H1 replayed on surrogate and simulated
  markets that share the window's statistics but have no exploitable
  structure or no drift; the share of such markets that match H1's recorded
  return and drawdown.
- `forward_calibration` (`sq.research.nulls`): the forward protocol's own
  false-GO rate: how often a market with no edge passes P1 and P2 after 30
  trades, and how long 30 trades take.
- `first_passage` (`sq.research.nulls`): the −20 % stop as a barrier problem
  of a random walk, analytic against simulated.
- `growth` (`sq.research.growth`): time-average growth, Kelly fraction with
  its uncertainty, growth-versus-drawdown frontier on the recorded marks.
- `ledger` (`sq.research.ledger`): how many times the data was looked at,
  and the deflated Sharpe ratio that charges for it.

None of this is evidence of an edge. The nulls sharpen the test and the
calibration says how much a future GO would mean; the held-out window was
inspected before these were added, so every figure on it is descriptive.
ADR-0008 predeclares the set of null models so that none is chosen after
seeing which one H1 beats.

Reproducibility: fixed seed, legacy `RandomState` streams, one stream per
model; each Monte Carlo section records the SHA-256 of its first
`PREFIX_TRIALS` per-trial outcomes so a test can rebuild that prefix cheaply
and detect drift without rerunning everything.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sq.research import breakout, growth, ledger, nulls, proxy_data, stylized

SCHEMA_VERSION = 1
DEFAULT_SEED = 20260924  # H1's predeclaration date, as statistics.json
DEFAULT_NULL_TRIALS = 1000
DEFAULT_CALIBRATION_TRIALS = 1000
DEFAULT_FIRST_PASSAGE_TRIALS = 20000
PREFIX_TRIALS = 25
NULL_RUNS = ("heldout-base", "validation-base", "train-base")
ALL_SECTIONS = ("stylized", "nulls", "calibration", "first_passage", "growth", "ledger")
FEES = {"base": 0.005, "stress": 0.01}
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER = REPO_ROOT / "research" / "ledger.json"

METHOD = {
    "stylized_facts": "Log returns of 4h closes per period; Hill tail index on the largest 5 % "
    "of losses and gains; Hurst exponent by DFA-1 with an iid shuffle range; permutation "
    "entropy of order 4; MRW intermittency from the log-covariance of absolute returns.",
    "null_models": "H1's exact rules (sq.research.breakout, verified against the record; "
    "StoplossGuard modelled, MaxDrawdown protection not) replayed on surrogates of the window's "
    "own candles (5-day block shuffle; IAAFT) and on paths from models fitted to the window "
    "(GBM; GARCH(1,1)-t with zero drift and with the window's sample-mean drift; MRW), with "
    "intrabar wicks resampled from the real candles and the same warm-up the real run had. "
    "Shares are over all trials. A fixed stake can lose more than its notional, so returns "
    "below -100 % and drawdowns above 100 % occur on synthetic paths.",
    "forward_calibration": "Models fitted to the full 2020-2026 history; 10-year paths; the window "
    "ends when the 30th trade closes; F3, P1 and P2 as in docs/forward-test.md, buy-and-hold on "
    "the forward report's basis. share_go under a zero-drift model is the protocol's false-GO "
    "rate; under the drifted model it is the pass rate in a rising market without timing skill.",
    "first_passage": "Probability that a driftless Brownian log price with the window's "
    "volatility crosses the -20 % stop within a horizon: closed form against simulated 4h paths.",
    "growth": "Time-average (log) growth against the ensemble mean per trade; Kelly fraction "
    "capped at 1 (no leverage) with a trade bootstrap; compounding frontier on the daily marks.",
    "ledger": "Deflated Sharpe ratio (Bailey and Lopez de Prado 2014) for the number of counted "
    "trials in research/ledger.json.",
}


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def periods_from_curves(curves: dict) -> dict[str, tuple[str, str]]:
    """Period name -> (start, end exclusive), from the base-cost runs of the record."""
    out = {}
    for key, run in curves["runs"].items():
        period, cost = key.rsplit("-", 1)
        if cost == "base":
            out[period] = (run["start"], run["end"])
    return out


def build_physics(
    curves: dict,
    candles: pd.DataFrame,
    ledger_data: dict,
    *,
    candles_sha256: str,
    curves_sha256: str,
    seed: int = DEFAULT_SEED,
    null_trials: int = DEFAULT_NULL_TRIALS,
    calibration_trials: int = DEFAULT_CALIBRATION_TRIALS,
    first_passage_trials: int = DEFAULT_FIRST_PASSAGE_TRIALS,
    null_runs: tuple[str, ...] = NULL_RUNS,
    sections: tuple[str, ...] | None = None,
) -> dict:
    """The physics.json document. `sections` restricts the build (tests use it)."""
    wanted = set(sections or ALL_SECTIONS)
    periods = periods_from_curves(curves)
    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "pair": curves["pair"],
        "timeframe": curves["timeframe"],
        "notional_eur": curves["notional_eur"],
        "seed": seed,
        "candles_sha256": candles_sha256,
        "equity_curves_sha256": curves_sha256,
        "prefix_trials": PREFIX_TRIALS,
        "method": METHOD,
    }
    if "stylized" in wanted:
        out["stylized_facts"] = stylized.stylized_facts(candles, periods, seed=seed)
    if "nulls" in wanted:
        out["null_models"] = {}
        for key in null_runs:
            run = curves["runs"][key]
            fee = FEES[key.rsplit("-", 1)[1]]
            start, end = pd.Timestamp(run["start"], tz="UTC"), pd.Timestamp(run["end"], tz="UTC")
            out["null_models"][key] = nulls.null_comparison(
                candles, start, end, fee, trials=null_trials, seed=seed, prefix=PREFIX_TRIALS
            )
    if "calibration" in wanted or "first_passage" in wanted:
        r_full = stylized.log_returns(candles["close"].to_numpy())
        wicks = nulls.wick_ratios(candles)
    if "calibration" in wanted:
        out["forward_calibration"] = nulls.forward_calibration(
            r_full, wicks, trials=calibration_trials, seed=seed, prefix=PREFIX_TRIALS
        )
    if "first_passage" in wanted:
        heldout = periods["heldout"]
        mask = (candles["date"] >= pd.Timestamp(heldout[0], tz="UTC")) & (
            candles["date"] < pd.Timestamp(heldout[1], tz="UTC")
        )
        r_heldout = stylized.log_returns(candles.loc[mask, "close"].to_numpy())
        sigma = float(np.std(r_heldout, ddof=1) * np.sqrt(6 * 365))
        fp = nulls.first_passage_check(sigma, trials=first_passage_trials, seed=seed)
        trades = curves["runs"]["heldout-base"]["trades"]
        hold_days = [
            (pd.Timestamp(t["close"]) - pd.Timestamp(t["open"])).total_seconds() / 86400
            for t in trades
        ]
        fp["observed_median_holding_days"] = round(float(np.median(hold_days)), 2)
        out["first_passage"] = fp
    if "growth" in wanted:
        out["growth"] = {
            key: growth.growth_report(run, seed=seed) for key, run in curves["runs"].items()
        }
    if "ledger" in wanted:
        values = np.asarray(curves["runs"]["heldout-base"]["strategy"], dtype=float)
        daily = values[1:] / values[:-1] - 1
        out["ledger"] = ledger.ledger_report(ledger_data, daily)
    return out


def write_physics(
    experiment: Path,
    out: Path | None = None,
    candles_path: Path = proxy_data.DEFAULT_PATH,
    ledger_path: Path = DEFAULT_LEDGER,
    **kwargs,
) -> Path:
    curves_raw = (experiment / "equity-curves.json").read_bytes()
    candles = proxy_data.load_candles(candles_path)
    document = build_physics(
        json.loads(curves_raw),
        candles,
        ledger.load_ledger(ledger_path),
        candles_sha256=proxy_data.sha256(candles_path),
        curves_sha256=_sha256_bytes(curves_raw),
        **kwargs,
    )
    target = out or (experiment / "physics.json")
    target.write_text(json.dumps(document, indent=1, allow_nan=False) + "\n")
    print(f"wrote {target}")
    return target


__all__ = ["build_physics", "write_physics", "periods_from_curves", "breakout"]
