"""Ledger validation and deflated Sharpe checks."""

import copy
import json
import math

import numpy as np
import pytest

from sq.research import ledger


def entry(i: int, sharpe: float | None, counted: bool = True, kind: str = "hypothesis") -> dict:
    return {
        "id": f"T{i}",
        "date": "2026-09-24",
        "kind": kind,
        "description": f"trial {i}",
        "period": "2024-01-01..2024-12-31",
        "sharpe_annualised": sharpe,
        "counted": counted,
    }


def sample(sharpes: list[float | None]) -> dict:
    return {
        "schema_version": 1,
        "as_of": "2026-10-08",
        "entries": [entry(i, s) for i, s in enumerate(sharpes)],
    }


def write(tmp_path, payload: dict):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    return path


def daily_returns() -> np.ndarray:
    rng = np.random.RandomState(7)
    return rng.normal(0.0008, 0.02, size=400)


@pytest.mark.parametrize(
    ("p", "z"), [(0.975, 1.959964), (0.5, 0.0), (0.84134474606854, 1.0), (0.999, 3.090232)]
)
def test_norm_ppf_known_quantiles(p, z):
    assert ledger.norm_ppf(p) == pytest.approx(z, abs=1e-6)
    assert ledger.norm_ppf(1 - p) == pytest.approx(-z, abs=1e-6)


def test_norm_ppf_round_trips_to_1e_10():
    for p in (0.01, 0.3, 0.9, 0.9999):
        assert ledger.norm_cdf(ledger.norm_ppf(p)) == pytest.approx(p, abs=1e-10)
    with pytest.raises(ValueError):
        ledger.norm_ppf(1.0)


def test_expected_max_sharpe_zero_for_one_trial_and_increasing():
    assert ledger.expected_max_sharpe(1, 0.01) == 0.0
    values = [ledger.expected_max_sharpe(n, 0.01) for n in (2, 5, 20, 100)]
    assert all(v > 0 for v in values)
    assert values == sorted(values)
    # Scales with the cross-trial standard deviation.
    assert ledger.expected_max_sharpe(10, 0.04) == pytest.approx(
        2 * ledger.expected_max_sharpe(10, 0.01)
    )


def test_deflated_sharpe_with_zero_benchmark_normal_returns():
    sr, t = 0.1, 400
    # Normal returns: skewness 0, excess kurtosis 0, so the variance term is 1 + SR^2 / 2.
    expected = 0.5 * (1 + math.erf(sr * math.sqrt(t - 1) / math.sqrt(1 + sr**2 / 2) / math.sqrt(2)))
    got = ledger.deflated_sharpe(sr, 0.0, t, 0.0, 0.0)
    assert got == pytest.approx(expected)
    assert 0 < got < 1
    assert ledger.deflated_sharpe(sr, 0.05, t, 0.0, 0.0) < got
    assert ledger.deflated_sharpe(0.0, 0.0, t, 0.0, 0.0) == pytest.approx(0.5)


def test_load_ledger_accepts_valid_and_rejects_invalid(tmp_path):
    good = sample([1.0, 0.5, 0.8])
    assert len(ledger.load_ledger(write(tmp_path, good))["entries"]) == 3

    wrong_version = copy.deepcopy(good)
    wrong_version["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        ledger.load_ledger(write(tmp_path, wrong_version))

    bad_kind = copy.deepcopy(good)
    bad_kind["entries"][0]["kind"] = "vibes"
    with pytest.raises(ValueError, match="kind"):
        ledger.load_ledger(write(tmp_path, bad_kind))

    no_counted = copy.deepcopy(good)
    del no_counted["entries"][1]["counted"]
    with pytest.raises(ValueError, match="counted"):
        ledger.load_ledger(write(tmp_path, no_counted))

    not_bool = copy.deepcopy(good)
    not_bool["entries"][1]["counted"] = 1
    with pytest.raises(ValueError, match="counted"):
        ledger.load_ledger(write(tmp_path, not_bool))

    bad_date = copy.deepcopy(good)
    bad_date["entries"][2]["date"] = "24/09/2026"
    with pytest.raises(ValueError, match="date"):
        ledger.load_ledger(write(tmp_path, bad_date))


def test_ledger_report_with_three_counted_entries_is_finite():
    report = ledger.ledger_report(sample([1.2, 0.4, 0.9]), daily_returns())
    assert report["trials_counted"] == 3
    assert report["daily_observations"] == 400
    for key in (
        "observed_sharpe_annualised",
        "observed_sharpe_daily",
        "skewness",
        "excess_kurtosis",
        "sharpe_variance_daily",
        "expected_max_sharpe_daily",
        "expected_max_sharpe_annualised",
        "deflated_sharpe_probability",
    ):
        assert report[key] is not None
        assert math.isfinite(report[key])
    assert 0 <= report["deflated_sharpe_probability"] <= 1
    assert report["expected_max_sharpe_annualised"] == pytest.approx(
        report["expected_max_sharpe_daily"] * math.sqrt(365), rel=1e-3
    )
    assert report["observed_sharpe_annualised"] == pytest.approx(
        report["observed_sharpe_daily"] * math.sqrt(365), rel=1e-3
    )
    assert [e["id"] for e in report["entries"]] == ["T0", "T1", "T2"]
    assert "not a posterior" in report["note"]


def test_ledger_report_uncounted_entries_do_not_count():
    data = sample([1.2, 0.4, 0.9])
    data["entries"].append(entry(3, 2.0, counted=False, kind="benchmark"))
    assert ledger.ledger_report(data, daily_returns())["trials_counted"] == 3


def test_ledger_report_single_counted_entry_has_null_variance_fields():
    data = sample([1.2])
    data["entries"].append(entry(1, 0.3, counted=False, kind="rerun"))
    report = ledger.ledger_report(data, daily_returns())
    assert report["trials_counted"] == 1
    assert report["observed_sharpe_annualised"] is not None
    for key in (
        "sharpe_variance_daily",
        "expected_max_sharpe_daily",
        "expected_max_sharpe_annualised",
        "deflated_sharpe_probability",
    ):
        assert report[key] is None


@pytest.mark.parametrize("mismatch", ["missing", "different_period"])
def test_incomparable_trials_do_not_produce_a_deflated_sharpe(mismatch):
    data = sample([1.2, 0.4, 0.9])
    if mismatch == "missing":
        data["entries"][1]["sharpe_annualised"] = None
    else:
        data["entries"][1]["period"] = "2020-01-01..2023-01-01"
    report = ledger.ledger_report(data, daily_returns())
    assert report["selection_adjustment_status"] == "unavailable"
    assert report["selection_adjustment_reasons"]
    assert report["sharpe_variance_daily"] is None
    assert report["deflated_sharpe_probability"] is None
    assert report["observed_sharpe_daily"] is not None
