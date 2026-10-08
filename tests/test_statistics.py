"""Tests for `sq.research.statistics` (pure numpy/pandas)."""

import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest
from conftest import REPO_ROOT

from sq.research import statistics as st

H1 = REPO_ROOT / "research" / "experiments" / "H1"


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="UTC")


def trade(open_: str, close: str, ret: float, exit_: str = "exit_signal") -> st.Trade:
    return st.Trade(open=ts(open_), close=ts(close), return_pct=ret, exit=exit_)


# --- daily series ----------------------------------------------------------


def test_daily_returns():
    assert st.daily_returns([100, 110, 99]) == pytest.approx([0.1, -0.1])


def test_max_drawdown_pct():
    assert st.max_drawdown_pct([100, 120, 90, 110]) == pytest.approx(25.0)
    assert st.max_drawdown_pct([1, 2, 3]) == 0.0


def test_longest_drawdown_days():
    assert st.longest_drawdown_days([1, 2, 1, 1, 3, 2, 3]) == 2
    assert st.longest_drawdown_days([1, 2, 1, 1, 1]) == 3  # unrecovered to the end
    assert st.longest_drawdown_days([1, 2, 3]) == 0


def test_sharpe_ratio():
    r = np.array([0.01, -0.01, 0.02, 0.0])
    expected = np.mean(r) / np.std(r, ddof=1) * math.sqrt(365)
    assert st.sharpe_ratio(r) == pytest.approx(expected)
    assert math.isnan(st.sharpe_ratio(np.array([0.01, 0.01, 0.01])))


def test_sortino_ratio():
    r = np.array([0.02, -0.01, 0.03, -0.02])
    downside = math.sqrt(np.mean([0, 0.01**2, 0, 0.02**2]))
    assert st.sortino_ratio(r) == pytest.approx(np.mean(r) / downside * math.sqrt(365))
    assert math.isnan(st.sortino_ratio(np.array([0.01, 0.02, 0.0])))


def test_beta_and_correlation():
    r = np.array([0.01, -0.02, 0.03, 0.0, 0.015])
    beta, corr = st.beta_and_correlation(r, r)
    assert beta == pytest.approx(1.0)
    assert corr == pytest.approx(1.0)
    beta2, corr2 = st.beta_and_correlation(2 * r, r)
    assert beta2 == pytest.approx(2.0)
    assert corr2 == pytest.approx(1.0)


def test_beta_and_correlation_edge_cases():
    with pytest.raises(ValueError, match="lengths differ"):
        st.beta_and_correlation(np.array([0.1, 0.2]), np.array([0.1, 0.2, 0.3]))
    beta, corr = st.beta_and_correlation(np.array([0.1, 0.2, 0.3]), np.zeros(3))
    assert math.isnan(beta) and math.isnan(corr)


def test_annualised_volatility():
    r = np.array([0.01, -0.01, 0.02])
    assert st.annualised_volatility_pct(r) == pytest.approx(
        np.std(r, ddof=1) * math.sqrt(365) * 100
    )


# --- trades ----------------------------------------------------------------

TRADES = [
    trade("2025-01-01 00:00", "2025-01-03 00:00", 10.0, "exit_signal"),
    trade("2025-02-01 00:00", "2025-02-02 00:00", -4.0, "stop_loss"),
    trade("2025-03-01 00:00", "2025-03-05 00:00", -2.0, "stop_loss"),
    trade("2025-04-01 00:00", "2025-04-02 12:00", 6.0, "exit_signal"),
    trade("2025-05-01 00:00", "2025-05-02 00:00", 1.0, "exit_signal"),
]


def test_trade_hours_and_parse():
    parsed = st.parse_trades(
        [{"open": "2025-01-01 00:00", "close": "2025-01-02 06:00", "return_pct": 2, "exit": "x"}]
    )
    assert parsed[0].hours == 30
    assert parsed[0].open == ts("2025-01-01 00:00")
    assert parsed[0].return_pct == 2.0


def test_trade_statistics():
    s = st.trade_statistics(TRADES)
    r = np.array([10, -4, -2, 6, 1], dtype=float)
    assert s["count"] == 5
    assert s["winners"] == 3
    assert s["win_rate_pct"] == 60.0
    assert s["mean_pct"] == pytest.approx(2.2)
    assert s["sd_pct"] == pytest.approx(round(float(np.std(r, ddof=1)), 4))
    assert s["t_stat"] == pytest.approx(round(2.2 / (np.std(r, ddof=1) / math.sqrt(5)), 4))
    assert s["sum_pct"] == 11.0
    assert s["profit_factor"] == pytest.approx(17 / 6, abs=1e-4)
    assert s["mean_win_pct"] == pytest.approx(17 / 3, abs=1e-4)
    assert s["mean_loss_pct"] == -3.0
    assert s["payoff_ratio"] == pytest.approx(17 / 9, abs=1e-4)
    assert s["top2_contribution_pp"] == 16.0
    assert s["rest_contribution_pp"] == -5.0
    assert s["longest_losing_streak"] == 2
    assert s["median_holding_days"] == 1.5
    assert s["mean_holding_days"] == pytest.approx((2 + 1 + 4 + 1.5 + 1) / 5)
    assert s["exit_reasons"] == {"exit_signal": 3, "stop_loss": 2}


def test_trade_statistics_all_winners():
    s = st.trade_statistics(TRADES[:1] + TRADES[3:4])
    assert s["winners"] == 2
    assert s["profit_factor"] is None
    assert s["mean_loss_pct"] is None
    assert s["payoff_ratio"] is None
    assert s["longest_losing_streak"] == 0


def test_trade_statistics_all_losers():
    s = st.trade_statistics(TRADES[1:3])
    assert s["winners"] == 0
    assert s["win_rate_pct"] == 0.0
    assert s["profit_factor"] == 0.0
    assert s["mean_win_pct"] is None
    assert s["payoff_ratio"] is None
    assert s["longest_losing_streak"] == 2


def test_trade_statistics_empty_and_single():
    assert st.trade_statistics([]) == {"count": 0}
    single = st.trade_statistics(TRADES[:1])
    assert single["sd_pct"] is None and single["t_stat"] is None


def test_longest_losing_streak_counts_zero_as_loss():
    assert st.longest_losing_streak([1, 0, -1, 2, -1]) == 2
    assert st.longest_losing_streak([]) == 0


def test_exposure_pct_clips_to_period():
    start, end = ts("2025-01-01"), ts("2025-01-11")
    inside = trade("2025-01-02", "2025-01-04", 0.0)
    overlapping = trade("2024-12-30", "2025-01-02", 0.0)  # 1 day inside
    outside = trade("2025-02-01", "2025-02-03", 0.0)
    assert st.exposure_pct([inside], start, end) == pytest.approx(20.0)
    assert st.exposure_pct([overlapping], start, end) == pytest.approx(10.0)
    assert st.exposure_pct([outside], start, end) == 0.0
    assert st.exposure_pct([inside, overlapping], start, end) == pytest.approx(30.0)


def test_exposure_pct_empty_period_raises():
    with pytest.raises(ValueError, match="non-empty"):
        st.exposure_pct([], ts("2025-01-01"), ts("2025-01-01"))


def test_holding_days_rounds_up_with_minimum_one():
    trades = [
        trade("2025-01-01 00:00", "2025-01-01 04:00", 0.0),
        trade("2025-01-01 00:00", "2025-01-03 00:00", 0.0),
        trade("2025-01-01 00:00", "2025-01-03 04:00", 0.0),
    ]
    assert st.holding_days(trades) == [1, 2, 2]  # 4 h -> 1 (minimum), 48 h -> 2, 52 h -> 2


# --- power -------------------------------------------------------------------


def test_power_analysis():
    p = st.power_analysis(2.0, 10.0, 5.0)
    assert p["trades_needed"] == math.ceil((2 * 10 / 2) ** 2) == 100
    assert p["years_needed"] == 20.0
    assert p["trades_per_year_observed"] == 5.0
    # ceil, not round
    assert st.power_analysis(3.0, 10.0, 4.0)["trades_needed"] == math.ceil((20 / 3) ** 2) == 45


def test_power_analysis_non_positive_mean():
    for mean in (0.0, -1.0, float("nan")):
        p = st.power_analysis(mean, 5.0, 4.0)
        assert p["trades_needed"] is None and p["years_needed"] is None
        assert "note" in p
    assert st.power_analysis(1.0, 0.0, 4.0)["trades_needed"] is None


def test_power_analysis_zero_frequency():
    assert st.power_analysis(2.0, 10.0, 0.0)["years_needed"] is None


# --- resampling -----------------------------------------------------------------


def test_bootstrap_trades_deterministic_and_contains_mean():
    r = [10.0, -4.0, -2.0, 6.0, 1.0, 3.0, -1.0]
    a = st.bootstrap_trades(r, resamples=500, seed=7)
    b = st.bootstrap_trades(r, resamples=500, seed=7)
    c = st.bootstrap_trades(r, resamples=500, seed=8)
    assert a == b
    assert a != c
    lo, hi = a["mean_ci95_pct"]
    assert lo <= np.mean(r) <= hi
    slo, shi = a["sum_ci95_pp"]
    assert slo <= sum(r) <= shi
    assert 0.0 <= a["p_mean_le_zero"] <= 1.0


def test_bootstrap_trades_too_few():
    out = st.bootstrap_trades([1.0], resamples=10, seed=1)
    assert out["note"] == "fewer than two trades"
    assert "mean_ci95_pct" not in out


def test_block_bootstrap_sharpe():
    rng = np.random.RandomState(3)
    r = rng.normal(0.001, 0.02, size=200)
    a = st.block_bootstrap_sharpe(r, block_days=10, resamples=100, seed=5)
    b = st.block_bootstrap_sharpe(r, block_days=10, resamples=100, seed=5)
    assert a == b
    lo, hi = a["sharpe_ci95"]
    assert isinstance(a["sharpe_ci95"], list) and len(a["sharpe_ci95"]) == 2
    assert lo <= hi
    assert 0.0 <= a["p_sharpe_le_zero"] <= 1.0


def test_block_bootstrap_sharpe_too_short():
    out = st.block_bootstrap_sharpe(np.zeros(10), block_days=20, resamples=10)
    assert out["note"] == "series too short"


# --- random timing --------------------------------------------------------------


def test_random_timing_trial_flat_no_fee():
    growth = np.ones(50)
    ret, dd = st.random_timing_trial(growth, [3, 5, 2], 0.0, 100.0, np.random.RandomState(1))
    assert ret == 0.0
    assert dd == 0.0


def test_random_timing_trial_flat_with_fee():
    fee = 0.005
    durations = [3, 5, 2]
    ret, dd = st.random_timing_trial(np.ones(50), durations, fee, 100.0, np.random.RandomState(1))
    assert ret == pytest.approx(len(durations) * ((1 - fee) ** 2 - 1) * 100)
    assert dd > 0


def test_random_timing_trial_does_not_mutate_durations():
    durations = [1, 2, 3, 4]
    st.random_timing_trial(np.ones(30), durations, 0.0, 1.0, np.random.RandomState(1))
    assert durations == [1, 2, 3, 4]


def test_random_timing_trial_exposure_conserved_single_trade():
    # One trade of d days at 1.1 growth per day, wherever it lands: 1.1**d - 1.
    ret, _ = st.random_timing_trial(np.full(40, 1.1), [7], 0.0, 100.0, np.random.RandomState(2))
    assert ret == pytest.approx((1.1**7 - 1) * 100)


def test_random_timing_trial_exposure_conserved_multiple_trades():
    # Fixed notional: each trade adds notional * (1.1**d - 1), so the sum of
    # those, not 1.1**sum(durations) - 1 (that would compound across trades).
    durations = [2, 3, 4]
    ret, _ = st.random_timing_trial(
        np.full(40, 1.1), durations, 0.0, 100.0, np.random.RandomState(2)
    )
    assert ret == pytest.approx(sum(1.1**d - 1 for d in durations) * 100)


def test_random_timing_trial_exactly_fills_period():
    ret, _ = st.random_timing_trial(np.full(5, 1.1), [2, 3], 0.0, 1.0, np.random.RandomState(0))
    assert ret == pytest.approx((1.1**2 - 1 + 1.1**3 - 1) * 100)


def test_random_timing_trial_too_long_raises():
    with pytest.raises(ValueError, match="do not fit"):
        st.random_timing_trial(np.ones(5), [3, 3], 0.0, 1.0, np.random.RandomState(0))


def _toy_series(days: int = 120) -> list[float]:
    rng = np.random.RandomState(11)
    return list(100 * np.cumprod(1 + rng.normal(0.001, 0.02, size=days)))


def test_random_timing_benchmark():
    values = _toy_series()
    trades = [
        trade("2025-01-05 00:00", "2025-01-10 00:00", 3.0),
        trade("2025-02-01 00:00", "2025-02-03 12:00", -1.0),
        trade("2025-03-01 00:00", "2025-03-02 00:00", 2.0),
    ]
    kwargs = dict(
        fee=0.005, notional=100.0, actual_return_pct=1.0, actual_drawdown_pct=5.0, trials=60, seed=4
    )
    a = st.random_timing_benchmark(values, trades, **kwargs)
    b = st.random_timing_benchmark(values, trades, **kwargs)
    assert a == b
    assert a["exposure_days"] == sum(st.holding_days(trades)) == 5 + 2 + 1
    assert a["trade_count"] == 3
    assert a["period_days"] == len(values) - 1
    assert 0.0 <= a["p_return_ge_actual"] <= 1.0
    assert 0.0 <= a["p_drawdown_le_actual"] <= 1.0
    assert a["net_return_pct"]["p5"] <= a["net_return_pct"]["p50"] <= a["net_return_pct"]["p95"]


def test_random_timing_benchmark_no_trades():
    out = st.random_timing_benchmark(
        _toy_series(30),
        [],
        fee=0.0,
        notional=1.0,
        actual_return_pct=0.0,
        actual_drawdown_pct=0.0,
        trials=5,
    )
    assert out == {"trials": 0, "note": "no trades"}


def test_random_timing_benchmark_scales_down_overlong_durations():
    values = [100.0] * 11  # 10 growth days
    trades = [trade("2025-01-01", "2025-01-09", 0.0), trade("2025-02-01", "2025-02-09", 0.0)]
    out = st.random_timing_benchmark(
        values, trades, fee=0.0, notional=1.0, actual_return_pct=0.0, actual_drawdown_pct=0.0,
        trials=3,
    )  # fmt: skip
    assert out["exposure_days"] <= out["period_days"] == 10


# --- constant exposure ------------------------------------------------------------


def test_constant_exposure_full_matches_buy_and_hold():
    values = _toy_series(60)
    out = st.constant_exposure_benchmark(values, 1.0, fee=0.0, notional=values[0])
    assert out["net_return_pct"] == pytest.approx((values[-1] / values[0] - 1) * 100, abs=1e-3)
    assert out["max_drawdown_pct"] == pytest.approx(st.max_drawdown_pct(values), abs=1e-3)


def test_constant_exposure_zero():
    out = st.constant_exposure_benchmark(_toy_series(60), 0.0, fee=0.005, notional=100.0)
    assert out["net_return_pct"] == 0.0
    assert out["max_drawdown_pct"] == 0.0
    assert out["exposure_fraction"] == 0.0


# --- calendar ---------------------------------------------------------------------


def test_yearly_returns():
    dates = ["2024-06-01", "2024-12-31", "2025-06-01", "2025-12-31"]
    rows = st.yearly_returns(dates, [100, 110, 100, 132], [100, 100, 150, 120])
    assert rows == [
        {
            "year": 2024,
            "from": "2024-06-01",
            "to": "2024-12-31",
            "strategy_pct": 10.0,
            "buy_hold_pct": 0.0,
        },
        {
            "year": 2025,
            "from": "2024-12-31",
            "to": "2025-12-31",
            "strategy_pct": 20.0,
            "buy_hold_pct": 20.0,
        },
    ]


# --- decision rule -----------------------------------------------------------------


def _runs(heldout_dd: float = 20.0, stress_ret: float = 1.0, validation_ret: float = 2.0) -> dict:
    return {
        "heldout-base": {
            "net_return_pct": 5.0,
            "mtm_max_drawdown_pct": heldout_dd,
            "buy_hold_max_drawdown_pct": 50.0,
        },
        "heldout-stress": {"net_return_pct": stress_ret},
        "validation-base": {"net_return_pct": validation_ret},
    }


def test_decision_check_go_and_binding():
    d = st.decision_check(_runs(heldout_dd=29.0))
    k2 = d["criteria"][1]
    assert k2["id"] == "K2"
    assert k2["threshold"] == 30.0
    assert k2["margin_pp"] == 1.0
    assert k2["pass"] is True
    assert d["verdict"] == "GO"
    assert d["binding"] == "K2"


def test_decision_check_failing_k2_is_no_go():
    d = st.decision_check(_runs(heldout_dd=31.0))
    assert d["criteria"][1]["pass"] is False
    assert d["criteria"][1]["margin_pp"] == -1.0
    assert d["verdict"] == "NO-GO"
    assert d["binding"] == "K2"


def test_decision_check_failing_return_criteria():
    d = st.decision_check(_runs(heldout_dd=10.0, stress_ret=-0.5))
    assert d["verdict"] == "NO-GO"
    assert d["binding"] == "K3"
    d = st.decision_check(_runs(heldout_dd=10.0, validation_ret=0.0))
    assert d["criteria"][3]["pass"] is False
    assert d["verdict"] == "NO-GO"


# --- recorded experiment ------------------------------------------------------------


@pytest.fixture(scope="module")
def curves() -> dict:
    return json.loads((H1 / "equity-curves.json").read_text())


def test_recorded_heldout_base(curves):
    out = st.build_statistics(
        curves, source_sha256="x", trade_resamples=200, sharpe_resamples=50, timing_trials=50
    )
    run = out["runs"]["heldout-base"]
    assert run["trades"]["count"] == 14
    assert run["trades"]["winners"] == 5
    assert run["trades"]["t_stat"] == pytest.approx(0.7307, abs=1e-3)
    assert run["trades"]["top2_contribution_pp"] == pytest.approx(65.46, abs=0.01)
    assert run["strategy"]["exposure_pct"] == pytest.approx(38.45, abs=0.01)
    assert out["decision"]["criteria"][1]["margin_pp"] == pytest.approx(0.9403, abs=1e-3)
    assert out["decision"]["verdict"] == "GO"
    assert out["decision"]["binding"] == "K2"


def test_recorded_statistics_not_drifted():
    raw = (H1 / "equity-curves.json").read_bytes()
    fresh = st.build_statistics(json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest())
    recorded = json.loads((H1 / "statistics.json").read_text())
    assert json.loads(json.dumps(fresh)) == recorded
