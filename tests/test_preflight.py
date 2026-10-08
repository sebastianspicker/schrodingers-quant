"""Tests for the pure part of `sq.live.preflight`."""

import dataclasses
import math

import pytest

from sq.live.preflight import MarketProfile, assess_feasibility, compute_spread_bps


def truncating_rounder(amount: float, precision: float | None, precision_mode: int) -> float:
    """Round DOWN to the tick. Mirrors ccxt TRUNCATE/TICK_SIZE semantics for
    these tests only; the production default is Freqtrade's amount_to_precision."""
    if precision is None:
        return amount
    return round(math.floor(amount / precision) * precision, 12)


# The recorded public preflight (docs/live-pilot.md section 7).
BASE = MarketProfile(
    pair="BTC/EUR",
    price=74221.3,
    bid=74221.2,
    ask=74221.3,
    amount_min=5e-05,
    cost_min=0.45,
    amount_precision=1e-08,
    price_precision=0.1,
    precision_mode=4,
    taker_fee=0.004,
    fee_source="test",
    account_fee_verified=True,
    stoploss_on_exchange_supported=True,
    eur_balance=None,
    eur_balance_source="test",
)


def assess(market: MarketProfile = BASE, stake: float = 8.0, stoploss: float = -0.20, **kwargs):
    return assess_feasibility(market, stake, stoploss, round_amount=truncating_rounder, **kwargs)


def with_(**changes) -> MarketProfile:
    return dataclasses.replace(BASE, **changes)


def test_recorded_public_preflight():
    report = assess()
    assert report.entry_amount == pytest.approx(0.00010778, abs=1e-12)
    assert report.sellable_amount == pytest.approx(0.00010734, abs=1e-12)
    assert report.max_loss_eur == pytest.approx(1.6515, abs=1e-3)
    assert report.stop_price == pytest.approx(74221.3 * 0.8)
    assert report.feasible is True
    assert report.reasons == []
    assert report.stake_dust_eur >= 0
    assert report.dust_base >= 0


def test_stake_below_cost_min():
    report = assess(stake=0.40)
    assert not report.feasible
    assert any("entry cost" in r and "below exchange minimum" in r for r in report.reasons)


def test_amount_rounds_to_zero():
    report = assess(with_(amount_precision=1.0), stake=8.0)
    assert not report.feasible
    assert report.entry_amount == 0
    assert any("rounds down to zero" in r for r in report.reasons)


def test_sellable_below_amount_min():
    # Entry amount exactly at the minimum; the entry fee pushes the sellable amount below it.
    market = with_(amount_min=0.00010778)
    report = assess(market)
    assert report.entry_amount >= 0.00010778
    assert not report.feasible
    assert any("sellable amount" in r for r in report.reasons)
    assert not any("entry amount" in r for r in report.reasons)


def test_entry_amount_below_amount_min():
    report = assess(with_(amount_min=1.0))
    assert any("entry amount" in r and "below exchange minimum" in r for r in report.reasons)


def test_exit_value_at_stop_below_cost_min():
    # Entry cost (about 8) clears 7.0 but the exit at the -20 % stop (about 6.4) does not.
    report = assess(with_(cost_min=7.0))
    assert not report.feasible
    assert any("exit value at the stop price" in r for r in report.reasons)
    assert not any("entry cost" in r for r in report.reasons)


def test_balance_below_stake():
    report = assess(with_(eur_balance=5.0))
    assert not report.feasible
    assert any("balance" in r and "less than the requested stake" in r for r in report.reasons)
    assert assess(with_(eur_balance=8.0)).feasible


def test_require_account_data_adds_both_reasons():
    market = with_(account_fee_verified=False, eur_balance=None)
    assert assess(market).feasible
    report = assess(market, require_account_data=True)
    assert not report.feasible
    assert report.reasons == [
        "account fee tier was not verified",
        "account quote-currency balance was not read",
    ]


def test_require_account_data_satisfied():
    market = with_(account_fee_verified=True, eur_balance=100.0)
    assert assess(market, require_account_data=True).feasible


def test_none_limits_are_ignored():
    market = with_(amount_min=None, cost_min=None)
    assert assess(market, stake=0.5).feasible


def test_spread_bps():
    assert compute_spread_bps(99.0, 101.0) == pytest.approx(200.0)
    assert compute_spread_bps(100.0, 100.0) == 0.0


def test_spread_bps_non_positive_mid():
    assert compute_spread_bps(0.0, 0.0) == math.inf
    assert compute_spread_bps(-1.0, 1.0) == math.inf
