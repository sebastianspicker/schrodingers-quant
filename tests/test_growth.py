"""Growth, Kelly and frontier checks against hand-derived values."""

import math

import numpy as np
import pytest

from sq.research import growth
from sq.research.statistics import max_drawdown_pct


def test_constant_trade_has_log_growth_but_no_volatility_drag():
    g = growth.trade_growth([10.0] * 5)
    assert g["n"] == 5
    assert g["mean_pct"] == pytest.approx(10.0)
    assert g["time_average_growth_pct"] == pytest.approx(100 * math.log(1.1), abs=1e-4)
    # No dispersion: the arithmetic/log conversion is not volatility drag.
    assert g["volatility_drag_pct"] == 0
    assert g["arithmetic_log_gap_pct"] > 0


def test_drag_grows_with_dispersion():
    calm = growth.trade_growth([5.0, 5.0, 5.0, 5.0])
    wild = growth.trade_growth([30.0, -20.0, 30.0, -20.0])  # same 5 % mean
    assert wild["mean_pct"] == pytest.approx(calm["mean_pct"])
    assert wild["volatility_drag_pct"] > calm["volatility_drag_pct"]


def test_total_loss_has_no_time_average():
    g = growth.trade_growth([10.0, -100.0])
    assert g["time_average_growth_pct"] is None


def test_kelly_matches_analytic_value():
    # Win +100 % or lose 50 % with p = 0.5: b = 1 (gain), a = 0.5 (loss),
    # f* = p / a - q / b = 0.5 / 0.5 - 0.5 / 1 = 0.5
    # (from 0.5 / (1 + f) = 0.25 / (1 - 0.5 f)).
    r = np.array([1.0, -0.5] * 20)
    assert growth.kelly_fraction(r) == pytest.approx(0.5, abs=0.0025)


def test_kelly_all_negative_is_zero_with_every_resample_at_zero():
    returns_pct = [-1.0, -2.0, -0.5, -3.0]
    assert growth.kelly_fraction(np.array(returns_pct) / 100) == 0.0
    report = growth.kelly_report(returns_pct, resamples=200)
    assert report["kelly_fraction"] == 0.0
    assert report["share_resamples_zero"] == 1.0


def test_capped_fraction_never_exceeds_cap_but_unconstrained_can():
    r = np.array([0.1, 0.2, 0.05, 0.15])  # always positive: growth rises with f
    assert growth.kelly_fraction(r, f_max=1.0) == 1.0
    assert growth.kelly_fraction(r, f_max=0.4) == pytest.approx(0.4)
    assert growth.kelly_fraction_unconstrained(r) > 1.0
    report = growth.kelly_report([10, 20, 5, 15, -2], resamples=300)
    assert report["kelly_ci95"][1] <= 1.0
    assert report["kelly_fraction"] <= 1.0
    assert report["kelly_fraction_unconstrained"] >= report["kelly_fraction"]


def test_kelly_report_growth_at_fractions_and_few_trades():
    returns_pct = [10.0, -5.0, 20.0, -10.0]
    report = growth.kelly_report(returns_pct, resamples=100)
    expected = float(np.mean(np.log1p(0.5 * np.array(returns_pct) / 100))) * 100
    assert report["growth_at_fractions_pct"]["0.5"] == pytest.approx(expected, abs=1e-4)
    assert set(report["growth_at_fractions_pct"]) == {"0.25", "0.5", "0.75", "1.0"}
    short = growth.kelly_report([5.0])
    assert short["n"] == 1
    assert short["kelly_fraction"] is None
    assert short["kelly_ci95"] is None


def test_kelly_report_is_deterministic():
    returns_pct = [12.0, -4.0, 7.0, -9.0, 3.0, 25.0, -6.0]
    assert growth.kelly_report(returns_pct, resamples=500) == growth.kelly_report(
        returns_pct, resamples=500
    )


def test_daily_growth_of_flat_series_is_zero():
    g = growth.daily_growth([1000.0] * 4, ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    assert g["annualised_mean_pct"] == 0
    assert g["annualised_growth_pct"] == 0
    assert g["volatility_drag_pct"] == 0
    assert g["years"] == pytest.approx(3 / 365, abs=1e-4)


def test_frontier_compounds_the_fixed_stake_pnl():
    values = [1000.0, 1100.0, 990.0, 1200.0, 900.0, 1000.0]
    full = growth.frontier(values, 1000.0)[-1]
    assert full["fraction"] == 1.0
    # Daily P&L on the notional: +10 %, -11 %, +21 %, -30 %, +10 %; compounded.
    expected = 1.1 * 0.89 * 1.21 * 0.70 * 1.1
    assert full["terminal_multiple"] == pytest.approx(expected, abs=1e-4)
    assert full["terminal_multiple"] != pytest.approx(values[-1] / values[0], abs=1e-3)
    path = np.cumprod([1, 1.1, 0.89, 1.21, 0.70, 1.1])
    assert full["max_drawdown_pct"] == pytest.approx(max_drawdown_pct(path), abs=1e-3)


def test_frontier_scales_down_drawdown_and_checks_growth():
    values = [1000.0, 1100.0, 990.0, 1200.0, 900.0, 1000.0]
    points = growth.frontier(values)
    assert [p["fraction"] for p in points] == [0.25, 0.5, 0.75, 1.0]
    drawdowns = [p["max_drawdown_pct"] for p in points]
    assert drawdowns == sorted(drawdowns)
    r = np.diff(np.array(values)) / 1000.0
    assert points[1]["annualised_growth_pct"] == pytest.approx(
        365 * 100 * float(np.mean(np.log1p(0.5 * r))), abs=1e-3
    )


def test_growth_report_keys():
    run = {
        "dates": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        "strategy": [1000.0, 1050.0, 1020.0, 1100.0],
        "trades": [{"return_pct": r} for r in (5.0, -3.0, 8.0)],
    }
    report = growth.growth_report(run, resamples=100)
    assert set(report) == {"trade_level", "daily_level", "frontier", "qualification"}
    assert set(report["trade_level"]) >= {
        "n",
        "mean_pct",
        "time_average_growth_pct",
        "volatility_drag_pct",
        "kelly",
    }
    assert len(report["frontier"]) == 4
    assert "do not establish an edge" in report["qualification"]
