"""Tests for H2: the pure sizing functions, the research strategy's signal
parity with H1 and its stake scaling, and the run definitions. Freqtrade's own
order handling is not covered (the base class is a stand-in, see conftest)."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_candles

from sq.research import h1, h2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "strategies"))

import H2VolTargetBreakout as strategy_module  # noqa: E402
from H2VolTargetBreakout import H2VolTargetBreakout  # noqa: E402

from H1ChannelBreakout import H1ChannelBreakout  # noqa: E402

ANNUAL = np.sqrt(6 * 365)


def random_walk(n: int = 500, seed: int = 1) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    return make_candles(list(100 * np.cumprod(1 + rng.normal(0, 0.01, size=n))), spread=0.3)


class FakeDataProvider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def get_analyzed_dataframe(self, pair: str, timeframe: str):
        return self.frame, None


def stake(strategy, proposed=100.0, min_stake=None, max_stake=1000.0) -> float:
    return strategy.custom_stake_amount(
        "BTC/EUR", None, 100.0, proposed, min_stake, max_stake, 1.0, None, "long"
    )


def strategy_with_fraction(fraction: float | None) -> H2VolTargetBreakout:
    strategy = H2VolTargetBreakout({})
    frame = pd.DataFrame({"h2_stake_fraction": [0.9, fraction]})
    strategy.dp = FakeDataProvider(frame)
    return strategy


def test_custom_stake_uses_the_signal_candle_in_both_modes():
    """Live: the analysed frame ends with the signal candle. Backtest: it ends
    with the entry candle. The date filter selects the signal candle in both."""
    dates = pd.date_range("2025-01-01", periods=3, freq="4h", tz="UTC")
    entry_time = dates[2]  # the entry candle opens at the signal candle's close
    live = pd.DataFrame({"date": dates[:2], "h2_stake_fraction": [0.9, 0.5]})
    backtest = pd.DataFrame({"date": dates, "h2_stake_fraction": [0.9, 0.5, 0.3]})
    for frame in (live, backtest):
        strategy = H2VolTargetBreakout({})
        strategy.dp = FakeDataProvider(frame)
        assert (
            strategy.custom_stake_amount(
                "BTC/EUR", entry_time, 100.0, 100.0, None, 1000.0, 1.0, None, "long"
            )
            == 50.0
        )


def test_volatility_constant_series():
    sigma = h2.realized_volatility(pd.Series([100.0] * 200))
    assert sigma.iloc[: h2.VOL_WINDOW].isna().all()
    assert (sigma.iloc[h2.VOL_WINDOW :] == 0).all()


def test_volatility_alternating_returns_and_no_future_data():
    r = 0.01
    signs = np.where(np.arange(300) % 2 == 0, 1.0, -1.0)
    closes = pd.Series(100 * np.exp(np.cumsum(signs * r)))
    sigma = h2.realized_volatility(closes)
    window = np.log(closes).diff().iloc[-h2.VOL_WINDOW :]
    expected = window.std(ddof=1) * ANNUAL
    assert abs(sigma.iloc[-1] - expected) < 1e-9
    assert abs(expected - r * ANNUAL * np.sqrt(120 / 119)) < 1e-9

    # Row i uses closes up to and including row i, never later rows.
    truncated = h2.realized_volatility(closes.iloc[:-1])
    pd.testing.assert_series_equal(truncated, sigma.iloc[:-1])
    changed = closes.copy()
    changed.iloc[-1] *= 1.5
    assert h2.realized_volatility(changed).iloc[-1] != sigma.iloc[-1]


def test_stake_fraction_rules():
    assert h2.stake_fraction(0.8) == 0.5
    assert h2.stake_fraction(0.2) == 1.0
    assert h2.stake_fraction(4.0) == 0.25
    assert h2.stake_fraction(float("nan")) == h2.STAKE_FLOOR
    assert h2.stake_fraction(0.0) == h2.STAKE_FLOOR


def test_populate_indicators_adds_columns():
    out = H2VolTargetBreakout({}).populate_indicators(random_walk(450), {})
    assert {"h2_sigma", "h2_stake_fraction"} <= set(out.columns)
    assert out["h2_stake_fraction"].between(0.25, 1.0).all()
    assert out["h2_stake_fraction"].iloc[0] == h2.STAKE_FLOOR
    assert out["h2_sigma"].iloc[-1] > 0


def test_local_helpers_agree_with_canonical():
    rng = np.random.RandomState(7)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=400))))
    local = strategy_module.realized_volatility(closes)
    np.testing.assert_allclose(local, h2.realized_volatility(closes), equal_nan=True)
    expected = [h2.stake_fraction(s) for s in local]
    np.testing.assert_allclose(strategy_module.stake_fractions(local), expected)
    assert strategy_module.stake_fractions(pd.Series([0.0, -1.0, np.nan])).tolist() == [0.25] * 3
    for name in ("SIGMA_TARGET", "VOL_WINDOW", "STAKE_FLOOR", "STAKE_CAP", "CANDLES_PER_YEAR"):
        assert getattr(strategy_module, name) == getattr(h2, name)


def test_signals_identical_to_h1():
    frame = random_walk(500)
    h1s, h2s = H1ChannelBreakout({}), H2VolTargetBreakout({})
    out1 = h1s.populate_exit_trend(
        h1s.populate_entry_trend(h1s.populate_indicators(frame.copy(), {}), {}), {}
    )
    out2 = h2s.populate_exit_trend(
        h2s.populate_entry_trend(h2s.populate_indicators(frame.copy(), {}), {}), {}
    )
    for col in ("enter_long", "exit_long"):
        pd.testing.assert_series_equal(out1[col], out2[col])
    assert out1["enter_long"].sum() > 0


def test_startup_candle_count():
    assert H2VolTargetBreakout.startup_candle_count >= 122


def test_custom_stake_scales_by_last_fraction():
    assert stake(strategy_with_fraction(0.5)) == 50.0


def test_custom_stake_bounds():
    assert stake(strategy_with_fraction(0.25), min_stake=40.0) == 40.0
    assert stake(strategy_with_fraction(1.0), max_stake=60.0) == 60.0


def test_custom_stake_falls_back_to_floor():
    no_dp = H2VolTargetBreakout({})
    no_dp.dp = None
    assert stake(no_dp) == 25.0
    empty = H2VolTargetBreakout({})
    empty.dp = FakeDataProvider(pd.DataFrame({"h2_stake_fraction": []}))
    assert stake(empty) == 25.0
    assert stake(strategy_with_fraction(float("nan"))) == 25.0


def test_run_definitions():
    names = [r.name for r in h2.TRAIN_RUNS + h2.VALIDATION_RUNS]
    assert names == [
        "h2-train-base-BTC_EUR",
        "h2-train-stress-BTC_EUR",
        "h2-validation-base-BTC_EUR",
        "h2-validation-stress-BTC_EUR",
    ]
    assert {r.period for r in h2.TRAIN_RUNS} == {"train"}
    assert {r.period for r in h2.VALIDATION_RUNS} == {"validation"}
    assert all(r.strategy == "H2VolTargetBreakout" and r.pair == h1.PAIR for r in h2.TRAIN_RUNS)
    assert [r.fee for r in h2.TRAIN_RUNS] == [h1.FEE_BASE, h1.FEE_STRESS]
    assert not hasattr(h2, "HELDOUT_RUNS")


# --- paired forward evaluation ------------------------------------------------------


def _h1_frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "open_date",
            "close_date",
            "amount",
            "open_rate",
            "fee_open",
            "fee_close",
            "profit_abs",
        ],
    )


def test_resize_trades_scales_amount_and_profit_by_the_signal_candle_fraction():
    candles = random_walk(400)
    step = pd.Timedelta(hours=4)
    open_date = candles["date"].iloc[300]
    frame = _h1_frame(
        [
            {
                "open_date": open_date,
                "close_date": candles["date"].iloc[320],
                "amount": 10.0,
                "open_rate": 100.0,
                "fee_open": 0.005,
                "fee_close": 0.005,
                "profit_abs": 50.0,
            }
        ]
    )
    sigma = h2.realized_volatility(candles["close"]).iloc[299]  # the signal candle
    expected = h2.stake_fraction(float(sigma))
    resized = h2.resize_trades(frame, candles, step)
    assert resized["h2_stake_fraction"].iloc[0] == pytest.approx(expected)
    assert resized["amount"].iloc[0] == pytest.approx(10.0 * expected)
    assert resized["profit_abs"].iloc[0] == pytest.approx(50.0 * expected)
    # A trade whose signal candle is outside the candle history gets the floor.
    early = frame.assign(open_date=[candles["date"].iloc[0]])
    assert h2.resize_trades(early, candles, step)["h2_stake_fraction"].iloc[0] == h2.STAKE_FLOOR


def test_paired_evaluation_criteria():
    candles = random_walk(400)
    step = pd.Timedelta(hours=4)
    frame = _h1_frame(
        [
            {
                "open_date": candles["date"].iloc[200],
                "close_date": candles["date"].iloc[220],
                "amount": 10.0,
                "open_rate": float(candles["close"].iloc[199]),
                "fee_open": 0.005,
                "fee_close": 0.005,
                "profit_abs": 80.0,
            },
            {
                "open_date": candles["date"].iloc[300],
                "close_date": candles["date"].iloc[330],
                "amount": 10.0,
                "open_rate": float(candles["close"].iloc[299]),
                "fee_open": 0.005,
                "fee_close": 0.005,
                "profit_abs": -30.0,
            },
        ]
    )
    result = h2.paired_evaluation(frame, candles, 1000.0, step)
    assert result["trades"] == 2 and len(result["stake_fractions"]) == 2
    assert result["h1"]["net_return_pct"] == pytest.approx(5.0)
    # Fractions in [0.25, 1]: H2's realised return is H1's scaled trade by trade.
    f = result["stake_fractions"]
    assert result["h2"]["net_return_pct"] == pytest.approx((80 * f[0] - 30 * f[1]) / 10, abs=1e-3)
    assert {c["id"] for c in result["criteria"]} == {"H2-1", "H2-2", "H2-3"}
    assert result["all_pass"] == all(c["pass"] for c in result["criteria"])
    assert all(0.25 <= x <= 1.0 for x in f)


def test_paired_evaluation_without_trades_is_flat():
    candles = random_walk(200)
    result = h2.paired_evaluation(_h1_frame([]), candles, 1000.0, pd.Timedelta(hours=4))
    assert result["h1"]["net_return_pct"] == 0 and result["h2"]["net_return_pct"] == 0
    assert result["criteria"][2]["pass"] is False  # H2-3 needs a positive return
