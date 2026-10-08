"""Hand-calculated desk checks, independent of Docker and backtest execution."""

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from sq.research import desk


def run() -> dict:
    return {
        "start": "2024-01-30",
        "end": "2024-02-03",
        "dates": ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"],
        "strategy": [900, 1200, 900, 1100],
        "buy_hold": [800, 1000, 1100, 1200],
        "mtm_max_drawdown_pct": 30,
        "buy_hold_max_drawdown_pct": 22,
        "trades": [{"return_pct": r} for r in (50, 20, -30, -10)],
    }


def experiment(tmp_path: Path) -> Path:
    curves = {"notional_eur": 1000, "pair": "BTC/EUR", "runs": {"test-base": run()}}
    raw = json.dumps(curves).encode()
    (tmp_path / "equity-curves.json").write_bytes(raw)
    stats = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "runs": {
            "test-base": {
                "bootstrap_trades": {"mean_ci95_pct": [-2, 4]},
                "bootstrap_sharpe": {"sharpe_ci95": [-1, 1]},
                "random_timing": {"p_return_ge_actual": 0.2},
            }
        },
    }
    (tmp_path / "statistics.json").write_text(json.dumps(stats))
    return tmp_path


def test_risk_includes_initial_loss_and_unrecovered_duration():
    risk = desk.curve_risk(
        [90, 80, 100, 90],
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        "2024-01-01",
        100,
    )
    assert risk == pytest.approx(
        {
            "return_pct": -10,
            "max_drawdown_pct_daily": 20,
            "current_drawdown_pct_daily": 10,
            "longest_underwater_days": 2,
            "current_underwater_days": 1,
        }
    )


def test_monthly_returns_include_initial_capital_and_reconcile():
    months = desk.monthly_returns(run(), 1000)
    assert [m["partial_month"] for m in months] == [True, True]
    assert months[0]["strategy_pct"] == pytest.approx(20)
    assert months[0]["buy_hold_pct"] == pytest.approx(0)
    assert months[0]["excess_pp"] == pytest.approx(20)
    assert months[1]["strategy_pct"] == pytest.approx(100 * (1100 / 1200 - 1))
    assert months[1]["buy_hold_pct"] == pytest.approx(20)
    for series, end in (("strategy", 1100), ("buy_hold", 1200)):
        growth = math.prod(1 + m[f"{series}_pct"] / 100 for m in months)
        assert growth == pytest.approx(end / 1000)


def test_sleeve_scales_pnl_without_daily_rebalance():
    scenarios = desk.sleeve_scenarios(run(), 1000, 10)
    half = scenarios[1]
    # Half sleeve marks: 950, 1100, 950, 1050. Peak-to-trough 150 / 1100.
    assert half["return_pct"] == pytest.approx(5)
    assert half["max_drawdown_pct_daily"] == pytest.approx(150 / 1100 * 100)
    assert half["max_drawdown_pct_daily"] != pytest.approx(25 / 2)
    assert half["current_underwater_days"] == 2
    # Four-day observed gain is 5%; simple annual cash rate = .05 * 365 / 4.
    rate = 0.05 * 365 / 4
    assert half["observed_average_annual_cash_return_pct"] == pytest.approx(rate * 100)
    assert half["annual_cost_eur"] == 120
    assert half["historical_cost_break_even_capital_eur"] == pytest.approx(120 / rate)
    assert half["capital_sensitivity"][0]["average_annual_cash_after_vps_eur"] == pytest.approx(
        1000 * rate - 120
    )


def test_cost_break_even_nonpositive_rate_is_undefined():
    losing = run()
    losing["strategy"][-1] = 900
    assert all(
        s["historical_cost_break_even_capital_eur"] is None
        for s in desk.sleeve_scenarios(losing, 1000, 10)
    )
    assert desk.sleeve_scenarios(run(), 1000, 0)[0]["historical_cost_break_even_capital_eur"] == 0


def test_concentration_is_descriptive_trade_sum():
    c = desk.concentration(run()["trades"])
    assert c["rounded_trade_sum_pp"] == 30
    assert c["top_two_winners_pp"] == 70
    assert c["without_top_two_winners_pp"] == -40
    assert desk.concentration([{"return_pct": -5}])["top_winners_removed"] == 0
    assert desk.concentration([])["rounded_trade_sum_pp"] == 0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "1000"])
def test_reject_bad_equity(bad):
    damaged = run()
    damaged["strategy"][0] = bad
    with pytest.raises(ValueError):
        desk.validate_run(damaged, "bad")


def test_reject_missing_misaligned_and_duplicate_dates():
    for dates in (
        ["2024-01-30", "2024-01-31", "2024-02-02"],
        ["2024-01-30", "2024-01-31", "2024-01-31", "2024-02-02"],
        ["2024-01-30", "2024-01-31", "2024-02-01"],
    ):
        damaged = run()
        damaged["dates"] = dates
        with pytest.raises(ValueError):
            desk.validate_run(damaged, "bad")
    damaged = run()
    damaged["strategy"].pop()
    with pytest.raises(ValueError, match="align"):
        desk.validate_run(damaged, "bad")


def test_reject_unbounded_period():
    damaged = run()
    damaged["end"] = "9999-01-01"
    with pytest.raises(ValueError, match="period"):
        desk.validate_run(damaged, "bad")


def test_evidence_bounds_and_tail_share():
    s = {
        "bootstrap_trades": {"mean_ci95_pct": [-2, 4]},
        "bootstrap_sharpe": {"sharpe_ci95": [-1, 1]},
        "random_timing": {"p_return_ge_actual": 0.2},
    }
    assert desk.evidence(s)["mean_trade_ci_includes_zero"]
    for ci in ([4, -2], [float("nan"), 1], [1]):
        damaged = copy.deepcopy(s)
        damaged["bootstrap_trades"]["mean_ci95_pct"] = ci
        with pytest.raises(ValueError):
            desk.evidence(damaged)
    s["random_timing"]["p_return_ge_actual"] = 1.1
    with pytest.raises(ValueError):
        desk.evidence(s)


def test_cli_writes_reviewable_reports_and_csv(tmp_path, capsys):
    source = experiment(tmp_path)
    out = tmp_path / "desk"
    desk.main(["--experiment", str(source), "--out-dir", str(out), "--monthly-cost-eur", "10"])
    payload = json.loads((out / "report.json").read_text())
    r = payload["runs"]["test-base"]
    assert r["recorded_strategy_drawdown_pct_4h"] == 30
    assert r["strategy"]["max_drawdown_pct_daily"] == 25
    assert r["growth"] is None  # no physics.json in the synthetic experiment
    assert payload["physics_sha256"] is None
    assert "No edge or capital approval" in capsys.readouterr().out
    assert "Retrospective constant fixed-stake" in (out / "report.md").read_text()
    assert "### Growth and sizing" in (out / "report.md").read_text()
    assert "physics.json is missing" in (out / "report.md").read_text()
    assert "strategy_pct,buy_hold_pct,excess_pp" in (out / "monthly-returns.csv").read_text()


def test_stale_physics_is_refused(tmp_path):
    source = experiment(tmp_path)
    (source / "physics.json").write_text(
        json.dumps({"equity_curves_sha256": "not-the-curves", "growth": {}})
    )
    with pytest.raises(ValueError, match="rebuild physics"):
        desk.build_report(source)


def test_growth_is_read_from_physics(tmp_path):
    source = experiment(tmp_path)
    curves_hash = hashlib.sha256((source / "equity-curves.json").read_bytes()).hexdigest()
    growth = {
        "trade_level": {
            "mean_pct": 1.0,
            "time_average_growth_pct": 0.9,
            "volatility_drag_pct": 0.1,
            "kelly": {
                "kelly_fraction": 0.5,
                "kelly_fraction_unconstrained": 0.5,
                "kelly_ci95": [0.0, 1.0],
                "share_resamples_zero": 0.1,
            },
        },
        "daily_level": {},
        "frontier": [
            {
                "fraction": 1.0,
                "annualised_growth_pct": 1.0,
                "max_drawdown_pct": 2.0,
                "terminal_multiple": 1.0,
            }
        ],
        "qualification": "descriptive",
    }
    (source / "physics.json").write_text(
        json.dumps({"equity_curves_sha256": curves_hash, "growth": {"test-base": growth}})
    )
    report = desk.build_report(source)
    assert report["runs"]["test-base"]["growth"] == growth
    assert "Kelly fraction 0.50" in desk.markdown(report)


def test_refuse_stale_statistics_without_outputs(tmp_path):
    source = experiment(tmp_path)
    with (source / "equity-curves.json").open("a") as stream:
        stream.write("\n")
    out = tmp_path / "desk"
    with pytest.raises(SystemExit):
        desk.main(["--experiment", str(source), "--out-dir", str(out)])
    assert not out.exists()


@pytest.mark.parametrize("cost", ["nan", "inf", "-1"])
def test_cli_rejects_invalid_cost(tmp_path, cost):
    source = experiment(tmp_path)
    with pytest.raises(SystemExit):
        desk.main(["--experiment", str(source), "--monthly-cost-eur", cost])


def test_archived_h1_remains_authoritative():
    h1 = Path(__file__).resolve().parents[1] / "research/experiments/H1"
    report = desk.build_report(h1)
    r = report["runs"]["heldout-base"]
    assert r["recorded_strategy_drawdown_pct_4h"] == 30.3692
    assert r["strategy"]["max_drawdown_pct_daily"] == pytest.approx(29.876, abs=0.001)
    assert r["concentration"]["without_top_two_winners_pp"] == pytest.approx(-25.84)
    assert r["evidence"]["mean_trade_ci_includes_zero"]
    assert "evaluated twice" in report["contamination"]
    assert len(r["monthly"]) == 27
    g = r["growth"]
    assert set(g) == {"trade_level", "daily_level", "frontier", "qualification"}
    assert set(g["trade_level"]["kelly"]) >= {
        "kelly_fraction",
        "kelly_ci95",
        "share_resamples_zero",
    }
    assert [p["fraction"] for p in g["frontier"]] == [0.25, 0.5, 0.75, 1.0]
    assert g == desk.build_report(h1)["runs"]["heldout-base"]["growth"]
