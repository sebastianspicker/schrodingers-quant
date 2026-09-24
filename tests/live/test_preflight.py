"""Pure-function tests for the live preflight feasibility check."""

from pathlib import Path

import pytest
from ccxt import DECIMAL_PLACES

from sq.live import preflight

FORBIDDEN_FRAGMENTS = [
    "createorder",
    "createlimit",
    "createmarket",
    "cancelorder",
    "cancelall",
    "editorder",
]


def normalize(source: str) -> str:
    return source.replace("_", "").lower()


def test_preflight_never_references_an_order_mutating_method():
    source = normalize(Path(preflight.__file__).read_text())
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in source, f"found forbidden order-mutating fragment: {fragment}"


def make_market(**overrides) -> preflight.MarketProfile:
    defaults = dict(
        pair="TEST/EUR",
        price=100.0,
        bid=99.0,
        ask=100.0,
        amount_min=0.01,
        cost_min=5.0,
        amount_precision=2,
        price_precision=2,
        precision_mode=DECIMAL_PLACES,
        taker_fee=0.0,
        fee_source="market default taker fee (no credentials)",
        stoploss_on_exchange_supported=True,
        eur_balance=None,
        eur_balance_source="not read (no credentials)",
    )
    defaults.update(overrides)
    return preflight.MarketProfile(**defaults)


def test_compute_spread_bps():
    assert preflight.compute_spread_bps(99.0, 100.0) == pytest.approx(1.0 / 99.5 * 10_000)


def test_feasible_profile():
    market = make_market(taker_fee=0.01)
    report = preflight.assess_feasibility(market, stake_eur=50.0, stoploss=-0.05)

    assert report.entry_amount == 0.5
    assert report.entry_cost_eur == 50.0
    assert report.feasible is True
    assert report.reasons == []


def test_amount_min_infeasible():
    market = make_market(amount_min=1.0, cost_min=None, taker_fee=0.0)
    report = preflight.assess_feasibility(market, stake_eur=50.0, stoploss=-0.05)

    assert report.feasible is False
    assert any("below exchange minimum" in reason for reason in report.reasons)
    assert any("entry amount" in reason for reason in report.reasons)


def test_cost_min_at_stop_infeasible():
    # Entry passes both minimums, but a 50% stop-out drops the exit value
    # below the exchange's cost minimum: the exit could be rejected.
    market = make_market(amount_min=0.01, cost_min=40.0, taker_fee=0.0)
    report = preflight.assess_feasibility(market, stake_eur=50.0, stoploss=-0.5)

    assert report.entry_cost_eur >= 40.0  # entry itself is fine
    assert report.feasible is False
    assert any("exit value at the stop price" in reason for reason in report.reasons)


def test_fee_in_base_remainder_dust_makes_exit_infeasible():
    # A 33% base-currency entry fee leaves a rounded-down remainder just
    # below the amount minimum, even though the raw entry amount clears it.
    market = make_market(amount_min=0.335, cost_min=None, taker_fee=0.33)
    report = preflight.assess_feasibility(market, stake_eur=50.0, stoploss=-0.05)

    assert report.entry_amount >= market.amount_min  # entry order itself is fine
    assert report.dust_base > 0
    assert report.sellable_amount < market.amount_min
    assert report.feasible is False
    assert any("sellable amount after entry fee" in reason for reason in report.reasons)


def test_precision_rounds_down_never_up():
    # stake / price = 0.336 exactly; nearest-rounding would give 0.34,
    # truncation (the only correct exchange semantics) must give 0.33.
    market = make_market(price=1000.0, amount_min=0.0, cost_min=None, taker_fee=0.0)
    report = preflight.assess_feasibility(market, stake_eur=336.0, stoploss=-0.05)

    assert report.entry_amount_requested == 0.336
    assert report.entry_amount == 0.33
