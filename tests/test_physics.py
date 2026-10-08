"""The tracked physics.json must be what `make physics` produces (ADR-0008).

A full rebuild takes minutes, so the test rebuilds the cheap sections exactly
and, for the Monte Carlo sections, only the first `prefix_trials` trials of
every model. Recorded outcome hashes verify integrity; continuous outcomes
allow small cross-platform numerical differences. Discrete outcomes, sources,
the seed and methods remain exact.
"""

import hashlib
import json
from copy import deepcopy

import pytest
from conftest import REPO_ROOT

from sq.research import ledger, nulls, physics, proxy_data

H1 = REPO_ROOT / "research" / "experiments" / "H1"
PHYSICS = H1 / "physics.json"


def assert_model_prefix(fresh, recorded, *, calibration=False):
    for model in (fresh, recorded):
        assert nulls._prefix_hash(model["prefix_outcomes"]) == model["prefix_sha256"]
    actual, expected = fresh["prefix_outcomes"], recorded["prefix_outcomes"]
    assert len(actual) == len(expected)
    for row, reference in zip(actual, expected, strict=True):
        if calibration:
            # Flags, lock counts and completion times on the candle grid are exact.
            assert row == reference
        else:
            # Percentage points: 0.001 pp is EUR 0.01 on EUR 1000. Allow only
            # optimizer/libm noise in returns and drawdowns, never count changes.
            assert row[:2] == pytest.approx(reference[:2], rel=0, abs=0.001)
            assert row[2:] == reference[2:]
    actual_fit, expected_fit = fresh.get("fit"), recorded.get("fit")
    if expected_fit is None:
        assert actual_fit is None
    else:
        assert actual_fit.keys() == expected_fit.keys()
        for key, value in expected_fit.items():
            # Nelder-Mead parameter xtol is 1e-5; summaries have six significant
            # digits. Keep the achieved likelihood to tighter rounding precision.
            tolerance = 1e-6 if key == "loglik" else 1e-4
            assert actual_fit[key] == pytest.approx(value, rel=tolerance, abs=1e-12), key


@pytest.mark.parametrize(
    ("calibration", "column", "delta"),
    [(False, 0, 0.002), (False, 1, 0.002), (False, 2, 1), (False, 3, 1)]
    + [(True, column, 1) for column in range(6)],
)
def test_prefix_comparison_rejects_material_drift(calibration, column, delta):
    rows = [[0, 1, 0, 0, 0, 0]] if calibration else [[1.0, 2.0, 3, 0]]
    recorded = {"prefix_outcomes": rows, "prefix_sha256": nulls._prefix_hash(rows)}
    fresh = deepcopy(recorded)
    fresh["prefix_outcomes"][0][column] += delta
    fresh["prefix_sha256"] = nulls._prefix_hash(fresh["prefix_outcomes"])
    with pytest.raises(AssertionError):
        assert_model_prefix(fresh, recorded, calibration=calibration)


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads(PHYSICS.read_text())


@pytest.fixture(scope="module")
def inputs() -> dict:
    raw = (H1 / "equity-curves.json").read_bytes()
    return {
        "curves": json.loads(raw),
        "curves_sha256": hashlib.sha256(raw).hexdigest(),
        "candles": proxy_data.load_candles(),
        "candles_sha256": proxy_data.sha256(),
        "ledger": ledger.load_ledger(physics.DEFAULT_LEDGER),
    }


def test_sources_match(recorded, inputs):
    assert recorded["schema_version"] == physics.SCHEMA_VERSION
    assert recorded["candles_sha256"] == inputs["candles_sha256"]
    assert recorded["equity_curves_sha256"] == inputs["curves_sha256"]
    assert recorded["seed"] == physics.DEFAULT_SEED
    assert recorded["prefix_trials"] == physics.PREFIX_TRIALS
    assert recorded["method"] == physics.METHOD


def test_cheap_sections_not_drifted(recorded, inputs):
    fresh = physics.build_physics(
        inputs["curves"],
        inputs["candles"],
        inputs["ledger"],
        candles_sha256=inputs["candles_sha256"],
        curves_sha256=inputs["curves_sha256"],
        sections=("stylized", "growth", "ledger", "first_passage"),
    )
    fresh = json.loads(json.dumps(fresh))
    for section in ("stylized_facts", "growth", "ledger", "first_passage"):
        assert fresh[section] == recorded[section], section


@pytest.mark.parametrize("run_key", physics.NULL_RUNS)
def test_null_model_prefixes_not_drifted(recorded, inputs, run_key):
    prefix = recorded["prefix_trials"]
    fresh = physics.build_physics(
        inputs["curves"],
        inputs["candles"],
        inputs["ledger"],
        candles_sha256=inputs["candles_sha256"],
        curves_sha256=inputs["curves_sha256"],
        sections=("nulls",),
        null_trials=prefix,
        null_runs=(run_key,),
    )
    fresh_models = fresh["null_models"][run_key]["models"]
    recorded_models = recorded["null_models"][run_key]["models"]
    assert list(fresh_models) == list(recorded_models)
    for key, model in recorded_models.items():
        assert_model_prefix(fresh_models[key], model)
    assert (
        recorded["null_models"][run_key]["observed"] == (fresh["null_models"][run_key]["observed"])
    )


def test_calibration_prefixes_not_drifted(recorded, inputs):
    prefix = recorded["prefix_trials"]
    fresh = physics.build_physics(
        inputs["curves"],
        inputs["candles"],
        inputs["ledger"],
        candles_sha256=inputs["candles_sha256"],
        curves_sha256=inputs["curves_sha256"],
        sections=("calibration",),
        calibration_trials=prefix,
    )
    fresh_models = fresh["forward_calibration"]["models"]
    recorded_models = recorded["forward_calibration"]["models"]
    fresh_protocol = {
        k: v for k, v in fresh["forward_calibration"]["protocol"].items() if k != "trials"
    }
    recorded_protocol = {
        k: v for k, v in recorded["forward_calibration"]["protocol"].items() if k != "trials"
    }
    assert fresh_protocol == recorded_protocol
    assert list(fresh_models) == list(recorded_models)
    for key, model in recorded_models.items():
        assert_model_prefix(fresh_models[key], model, calibration=True)


def test_shares_are_fractions(recorded):
    for run in recorded["null_models"].values():
        for model in run["models"].values():
            for key in (
                "share_return_ge",
                "share_drawdown_le",
                "share_both",
                "share_paths_with_max_drawdown_lock",
            ):
                assert 0 <= model[key] <= 1
            assert model["share_both"] <= min(model["share_return_ge"], model["share_drawdown_le"])
    for model in recorded["forward_calibration"]["models"].values():
        assert 0 <= model["share_paths_with_max_drawdown_lock"] <= 1
        assert 0 <= model["share_go"] <= model["share_reached_required_trades"] <= 1
    assert set(recorded["null_models"]) == set(physics.NULL_RUNS)
    assert set(recorded["forward_calibration"]["models"]) == set(nulls.MODELS)
