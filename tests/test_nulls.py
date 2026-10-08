"""Null models: surrogates keep what they claim to keep, the fitters recover
known parameters, the first-passage formula matches simulation, and the
forward-protocol gates do the arithmetic the protocol states.
"""

import math

import numpy as np
import pandas as pd
import pytest

from sq.research import h1, nulls, proxy_data

HELDOUT = (pd.Timestamp(h1.HELDOUT_START, tz="UTC"), pd.Timestamp(h1.HELDOUT_END, tz="UTC"))


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    return proxy_data.load_candles()


def _acf(x: np.ndarray, lag: int) -> float:
    x = x - x.mean()
    return float((x[:-lag] * x[lag:]).mean() / x.var())


def test_wick_ratios_nonnegative_and_rebuild_ohlc(candles):
    sample = candles.iloc[:500].reset_index(drop=True)
    wicks = nulls.wick_ratios(sample)
    assert wicks.shape == (500, 2)
    assert (wicks >= 0).all()
    rng = np.random.RandomState(1)
    built = nulls.candles_from_returns(
        nulls.log_returns(sample), wicks, rng, sample["date"].iloc[0], float(sample["open"][0])
    )
    assert np.allclose(built["close"], sample["close"])
    assert (built["high"] >= built[["open", "close"]].max(axis=1)).all()
    assert (built["low"] <= built[["open", "close"]].min(axis=1)).all()


def test_block_shuffle_preserves_returns_and_dates(candles):
    sample = candles.iloc[:1000].reset_index(drop=True)
    shuffled = nulls.block_shuffle(sample, np.random.RandomState(2), block=30)
    assert shuffled["date"].equals(sample["date"])
    assert np.allclose(
        np.sort(nulls.log_returns(shuffled)), np.sort(nulls.log_returns(sample)), atol=1e-12
    )
    assert not np.allclose(nulls.log_returns(shuffled), nulls.log_returns(sample))


def test_iaaft_keeps_values_and_spectrum():
    rng = np.random.RandomState(3)
    white = rng.standard_t(5, size=2048)
    x = np.zeros_like(white)
    for t in range(1, len(x)):
        x[t] = 0.8 * x[t - 1] + white[t]
    y = nulls.iaaft(x, np.random.RandomState(4))
    assert np.array_equal(np.sort(y), np.sort(x))
    px, py = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2
    assert np.linalg.norm(px - py) / np.linalg.norm(px) < 0.1


def test_iaaft_candles_keep_dates(candles):
    sample = candles.iloc[:600].reset_index(drop=True)
    out = nulls.iaaft_candles(sample, np.random.RandomState(5))
    assert out["date"].equals(sample["date"])
    assert len(out) == len(sample)


def test_nelder_mead_minimises_quadratic():
    target = np.array([1.5, -2.0, 0.3])
    x, fx = nulls.nelder_mead(lambda p: float(((p - target) ** 2).sum()), np.zeros(3))
    assert fx < 1e-6
    assert np.allclose(x, target, atol=1e-3)


def test_garch_fit_recovers_parameters_and_clusters():
    true = {"mu": 0.0, "omega": 2e-6, "alpha": 0.08, "beta": 0.9, "nu": 5.0}
    r = nulls.simulate_garch_t(true, 6000, np.random.RandomState(6), drift=False)
    assert _acf(r**2, 1) > 0.05
    fit = nulls.fit_garch_t(r)
    assert abs(fit["persistence"] - 0.98) < 0.08
    assert 3 <= fit["nu"] <= 10
    assert fit["omega"] > 0


def test_mrw_fit_recovers_lambda2():
    params = {"sigma": 0.01, "lambda2": 0.03, "integral_scale": 1000.0}
    r = nulls.simulate_mrw(params, 20000, np.random.RandomState(7))
    assert 0.9 < r.std() / 0.01 < 1.1
    assert 0.015 <= nulls.fit_mrw(r)["lambda2"] <= 0.06
    iid = np.random.RandomState(8).standard_normal(20000)
    assert nulls.fit_mrw(iid)["lambda2"] < 0.02


def test_stop_hit_probability_formula_and_simulation():
    sigma, days = 0.6, 30
    b = -math.log(0.8)
    root = sigma * math.sqrt(days / 365)
    expected = 2 * 0.5 * (1 + math.erf((-b / root) / math.sqrt(2)))
    assert nulls.stop_hit_probability(sigma, days) == pytest.approx(expected)
    check = nulls.first_passage_check(sigma, horizons_days=(days,), trials=2000, seed=1)
    assert abs(check["simulated"]["30"] - check["analytic"]["30"]) < 0.03
    # a positive drift makes the stop less likely
    assert nulls.stop_hit_probability(sigma, days, drift_annual=1.0) < expected


def test_judge_window_gates():
    idx = pd.date_range("2030-01-01", periods=4, freq="4h")
    flat = pd.Series([1000.0] * 4, index=idx)
    hold = pd.Series([1000.0, 900.0, 800.0, 900.0], index=idx)  # drawdown 20 %

    def judge(values, reached=True, hold=hold):
        series = pd.Series(values, index=idx)
        return nulls.judge_window(series, hold, 1000.0, 31.31, 0.6, reached)

    go = judge([1000.0, 1050.0, 1020.0, 1100.0])
    assert go["go"] and not go["f3_stop"] and go["p1_pass"] and go["p2_pass"]
    assert not judge([1000.0, 1050.0, 1020.0, 1100.0], reached=False)["go"]
    stop = judge([1000.0, 650.0, 900.0, 1100.0])
    assert stop["f3_stop"] and not stop["go"]
    p1 = judge([1000.0, 1000.0, 990.0, 990.0])
    assert not p1["p1_pass"] and not p1["go"]
    p2 = judge([1000.0, 800.0, 900.0, 1100.0])  # 20 % > 0.6 * 20 %
    assert not p2["p2_pass"] and p2["p1_pass"] and not p2["go"]
    # buy-and-hold that never draws down: only a strategy with no drawdown passes P2
    assert nulls.judge_window(flat, flat, 1000.0, 31.31, 0.6, True)["p2_pass"]


def test_null_comparison_complete_and_deterministic(candles):
    start, end = HELDOUT
    args = (candles, start, end, 0.005, 10, 11)
    first = nulls.null_comparison(*args)
    second = nulls.null_comparison(*args)
    assert first == second
    assert set(first) == {"observed", "trials", "block_candles", "models"}
    assert set(first["models"]) == {
        "block_shuffle",
        "iaaft",
        "gbm_zero_drift",
        "garch_t_zero_drift",
        "garch_t_drift",
        "mrw_zero_drift",
    }
    keys = {
        "label",
        "share_return_ge",
        "share_drawdown_le",
        "share_both",
        "return_pct",
        "drawdown_pct",
        "trades",
        "share_paths_with_stop_exit",
        "fit",
    }
    for model in first["models"].values():
        assert set(model) == keys
        for share in ("share_return_ge", "share_drawdown_le", "share_both"):
            assert 0 <= model[share] <= 1
        assert set(model["return_pct"]) == {"p5", "p50", "p95"}


def test_prefix_hash_independent_of_trial_count(candles):
    start, end = HELDOUT
    short = nulls.null_comparison(
        candles,
        start,
        end,
        0.005,
        4,
        12,
        models={"gbm_zero_drift": nulls.MODELS["gbm_zero_drift"]},
        prefix=4,
    )
    long = nulls.null_comparison(
        candles,
        start,
        end,
        0.005,
        8,
        12,
        models={"gbm_zero_drift": nulls.MODELS["gbm_zero_drift"]},
        prefix=4,
    )
    assert short["models"].keys() == long["models"].keys()
    for key in short["models"]:
        assert short["models"][key]["prefix_sha256"] == long["models"][key]["prefix_sha256"]


def test_forward_calibration_end_to_end(candles):
    r = nulls.log_returns(candles.iloc[-3000:].reset_index(drop=True))
    wicks = nulls.wick_ratios(candles.iloc[-3000:])
    result = nulls.forward_calibration(
        r, wicks, trials=5, seed=13, max_years=3, trades_required=5, prefix=3
    )
    assert set(result) == {"protocol", "models"}
    assert set(result["models"]) == set(nulls.MODELS)
    for model in result["models"].values():
        for share in (
            "share_reached_required_trades",
            "share_f3_stop",
            "share_p1_pass",
            "share_p2_pass",
            "share_go",
            "share_paths_with_stop_exit",
        ):
            assert 0 <= model[share] <= 1
        assert model["share_go"] <= model["share_reached_required_trades"]
        assert "prefix_sha256" in model
    longer = nulls.forward_calibration(
        r, wicks, trials=7, seed=13, max_years=3, trades_required=5, prefix=3
    )
    for key in result["models"]:
        assert result["models"][key]["prefix_sha256"] == longer["models"][key]["prefix_sha256"]
