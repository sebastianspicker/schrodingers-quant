"""Tests for the read-only reconciliation between the trade DB and Kraken history."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from freqtrade.persistence import Trade, init_db
from freqtrade.persistence.trade_model import Order

from sq.live import reconcile

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


def test_reconcile_never_references_an_order_mutating_method():
    source = normalize(Path(reconcile.__file__).read_text())
    for fragment in FORBIDDEN_FRAGMENTS:
        assert fragment not in source, f"found forbidden order-mutating fragment: {fragment}"


# --- Pure reconcile() tests, with fixture dataclasses -----------------------


def db_order(**overrides) -> reconcile.DbOrder:
    defaults = dict(
        order_id="EX-1",
        pair="BTC/EUR",
        side="buy",
        status="closed",
        is_open=False,
        filled=0.5,
        amount=0.5,
        cost=50.0,
        fee_base=0.0013,
    )
    defaults.update(overrides)
    return reconcile.DbOrder(**defaults)


def exchange_order(**overrides) -> reconcile.ExchangeOrder:
    defaults = dict(
        order_id="EX-1",
        pair="BTC/EUR",
        side="buy",
        status="closed",
        filled=0.5,
        amount=0.5,
        cost=50.0,
        fee_cost=0.0013,
        fee_currency="BTC",
    )
    defaults.update(overrides)
    return reconcile.ExchangeOrder(**defaults)


def test_clean_match_has_no_mismatches():
    mismatches = reconcile.reconcile(
        [db_order()],
        [exchange_order()],
        base_balance=0.5,
        open_trade_base_amount=0.5,
        base_currency="BTC",
    )
    assert mismatches == []


def test_missing_db_order_is_reported():
    mismatches = reconcile.reconcile(
        [], [exchange_order()], base_balance=0.5, open_trade_base_amount=0.5, base_currency="BTC"
    )
    assert len(mismatches) == 1
    assert mismatches[0].kind == "missing_db_order"


def test_amount_mismatch_is_reported():
    mismatches = reconcile.reconcile(
        [db_order(filled=0.4)],
        [exchange_order(filled=0.5)],
        base_balance=0.5,
        open_trade_base_amount=0.5,
        base_currency="BTC",
    )
    assert any(m.kind == "amount_mismatch" for m in mismatches)


def test_stale_open_db_order_is_reported():
    mismatches = reconcile.reconcile(
        [db_order(is_open=True, status="open")],
        [exchange_order(status="closed")],
        base_balance=0.5,
        open_trade_base_amount=0.5,
        base_currency="BTC",
    )
    assert any(m.kind == "stale_open_order" for m in mismatches)


def test_dust_is_reported():
    mismatches = reconcile.reconcile(
        [db_order()],
        [exchange_order()],
        base_balance=0.51,
        open_trade_base_amount=0.5,
        base_currency="BTC",
        dust_tolerance=1e-6,
    )
    assert any(m.kind == "dust" for m in mismatches)


def test_oldest_open_order_date_ignores_closed_orders():
    now = datetime.now(UTC)
    orders = [
        db_order(order_id="A", is_open=False, order_date=now - timedelta(days=5)),
        db_order(order_id="B", is_open=True, order_date=now - timedelta(hours=1)),
    ]
    assert reconcile.oldest_open_order_date(orders) == now - timedelta(hours=1)


def test_oldest_open_order_date_picks_the_earliest_open_order():
    now = datetime.now(UTC)
    orders = [
        db_order(order_id="A", is_open=True, order_date=now - timedelta(hours=1)),
        db_order(order_id="B", is_open=True, order_date=now - timedelta(days=3)),
    ]
    assert reconcile.oldest_open_order_date(orders) == now - timedelta(days=3)


def test_oldest_open_order_date_is_none_without_open_orders():
    orders = [db_order(is_open=False)]
    assert reconcile.oldest_open_order_date(orders) is None


def test_compute_effective_since_widens_for_a_stale_open_order():
    base_since = datetime.now(UTC) - timedelta(hours=24)
    stale_open = base_since - timedelta(days=10)
    assert reconcile.compute_effective_since(base_since, stale_open) == stale_open


def test_compute_effective_since_keeps_base_since_without_a_stale_order():
    base_since = datetime.now(UTC) - timedelta(hours=24)
    recent_open = base_since + timedelta(hours=1)
    assert reconcile.compute_effective_since(base_since, recent_open) == base_since


def test_compute_effective_since_keeps_base_since_without_any_open_order():
    base_since = datetime.now(UTC) - timedelta(hours=24)
    assert reconcile.compute_effective_since(base_since, None) == base_since


def test_dust_within_tolerance_is_not_reported():
    mismatches = reconcile.reconcile(
        [db_order()],
        [exchange_order()],
        base_balance=0.5000001,
        open_trade_base_amount=0.5,
        base_currency="BTC",
        dust_tolerance=1e-6,
    )
    assert mismatches == []


def test_open_exchange_order_is_not_compared_as_filled():
    # An order still open on the exchange is not a "missing" or mismatched
    # closed fill; it simply has not closed yet.
    mismatches = reconcile.reconcile(
        [],
        [exchange_order(status="open")],
        base_balance=0.0,
        open_trade_base_amount=0.0,
        base_currency="BTC",
    )
    assert mismatches == []


# --- read_db_orders() against a real Freqtrade-schema fixture DB -----------


@pytest.fixture
def fixture_db_url(tmp_path) -> str:
    db_path = tmp_path / "fixture.sqlite"
    db_url = f"sqlite:///{db_path}"
    init_db(db_url)

    trade = Trade(
        exchange="kraken",
        pair="BTC/EUR",
        base_currency="BTC",
        stake_currency="EUR",
        is_open=True,
        fee_open=0.0026,
        fee_close=0.0026,
        open_rate=100.0,
        open_trade_value=50.0,
        stake_amount=50.0,
        amount=0.5,
        open_date=datetime.now(UTC),
        amount_requested=0.5,
    )
    Trade.session.add(trade)
    Trade.session.flush()

    order = Order(
        ft_trade_id=trade.id,
        ft_order_side="buy",
        ft_pair="BTC/EUR",
        ft_is_open=False,
        ft_amount=0.5,
        ft_price=100.0,
        order_id="EX-1",
        status="closed",
        symbol="BTC/EUR",
        order_type="limit",
        side="buy",
        price=100.0,
        average=100.0,
        amount=0.5,
        filled=0.5,
        remaining=0.0,
        cost=50.0,
        order_date=datetime.now(UTC),
        ft_fee_base=0.0013,
    )
    Trade.session.add(order)

    stale_open_order = Order(
        ft_trade_id=trade.id,
        ft_order_side="buy",
        ft_pair="BTC/EUR",
        ft_is_open=True,
        ft_amount=0.1,
        ft_price=100.0,
        order_id="EX-2",
        status="open",
        symbol="BTC/EUR",
        order_type="limit",
        side="buy",
        price=100.0,
        average=None,
        amount=0.1,
        filled=0.0,
        remaining=0.1,
        cost=0.0,
        order_date=datetime.now(UTC) - timedelta(days=10),
        ft_fee_base=0.0,
    )
    Trade.session.add(stale_open_order)
    Trade.session.commit()

    return db_url


def test_read_db_orders_reads_the_real_schema(fixture_db_url):
    db_orders, open_trade_base_amounts = reconcile.read_db_orders(fixture_db_url, ["BTC/EUR"])

    assert len(db_orders) == 2
    order = next(o for o in db_orders if o.order_id == "EX-1")
    assert order.order_id == "EX-1"
    assert order.pair == "BTC/EUR"
    assert order.side == "buy"
    assert order.status == "closed"
    assert order.is_open is False
    assert order.filled == 0.5
    assert order.amount == 0.5
    assert order.cost == 50.0
    assert order.fee_base == 0.0013
    assert order.order_date is not None
    assert order.order_date.tzinfo is not None
    assert open_trade_base_amounts == {"BTC/EUR": 0.5}


def test_read_db_orders_and_oldest_open_order_date_widen_the_since_cursor(fixture_db_url):
    db_orders, _ = reconcile.read_db_orders(fixture_db_url, ["BTC/EUR"])

    stale_open = next(o for o in db_orders if o.order_id == "EX-2")
    assert stale_open.is_open is True

    oldest_open = reconcile.oldest_open_order_date(db_orders)
    assert oldest_open == stale_open.order_date

    base_since = datetime.now(UTC) - timedelta(hours=24)
    effective_since = reconcile.compute_effective_since(base_since, oldest_open)
    assert effective_since == oldest_open
    assert effective_since < base_since


def test_db_path_from_url_rejects_non_sqlite():
    with pytest.raises(ValueError, match="sqlite"):
        reconcile.db_path_from_url("postgresql://user:pass@host/db")


def test_db_path_from_url_extracts_absolute_path():
    assert reconcile.db_path_from_url("sqlite:////freqtrade/user_data/runtime/live.sqlite") == (
        "/freqtrade/user_data/runtime/live.sqlite"
    )


def test_read_db_orders_reports_open_amounts_per_pair(fixture_db_url):
    # Pairs without open trades get 0.0; amounts are never summed across assets.
    _, open_amounts = reconcile.read_db_orders(fixture_db_url, ["BTC/EUR", "ETH/EUR"])
    assert open_amounts == {"BTC/EUR": 0.5, "ETH/EUR": 0.0}
