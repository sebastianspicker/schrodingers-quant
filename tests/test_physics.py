"""The tracked physics.json must be what `make physics` produces (ADR-0008).

A full rebuild takes minutes, so the test rebuilds the cheap sections exactly
and, for the Monte Carlo sections, only the first `prefix_trials` trials of
every model, whose outcome hash the record carries. A drift in the candles,
the curves, the ledger, the seed or any method changes at least one of these.
"""

import hashlib
import json

import pytest
from conftest import REPO_ROOT

from sq.research import ledger, nulls, physics, proxy_data

H1 = REPO_ROOT / "research" / "experiments" / "H1"
PHYSICS = H1 / "physics.json"


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


def test_null_model_prefixes_not_drifted(recorded, inputs):
    prefix = recorded["prefix_trials"]
    fresh = physics.build_physics(
        inputs["curves"],
        inputs["candles"],
        inputs["ledger"],
        candles_sha256=inputs["candles_sha256"],
        curves_sha256=inputs["curves_sha256"],
        sections=("nulls",),
        null_trials=prefix,
        null_runs=("heldout-base",),
    )
    fresh_models = fresh["null_models"]["heldout-base"]["models"]
    recorded_models = recorded["null_models"]["heldout-base"]["models"]
    assert list(fresh_models) == list(recorded_models)
    for key, model in recorded_models.items():
        assert fresh_models[key]["prefix_sha256"] == model["prefix_sha256"], key
        assert fresh_models[key].get("fit") == model.get("fit"), key
    assert (
        recorded["null_models"]["heldout-base"]["observed"]
        == (fresh["null_models"]["heldout-base"]["observed"])
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
        assert fresh_models[key]["prefix_sha256"] == model["prefix_sha256"], key
        assert fresh_models[key].get("fit") == model.get("fit"), key


def test_shares_are_fractions(recorded):
    for run in recorded["null_models"].values():
        for model in run["models"].values():
            for key in ("share_return_ge", "share_drawdown_le", "share_both"):
                assert 0 <= model[key] <= 1
            assert model["share_both"] <= min(model["share_return_ge"], model["share_drawdown_le"])
    for model in recorded["forward_calibration"]["models"].values():
        assert 0 <= model["share_go"] <= model["share_reached_required_trades"] <= 1
    assert set(recorded["null_models"]) == set(physics.NULL_RUNS)
    assert set(recorded["forward_calibration"]["models"]) == set(nulls.MODELS)
