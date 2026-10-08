"""Tests for the pure and sqlite parts of `sq.live.reconcile`."""

import sqlite3
from datetime import UTC, datetime

import pytest

from sq.live.reconcile import (
    DbOrder,
    ExchangeOrder,
    IncompleteExchangeHistoryError,
    compute_effective_since,
    db_path_from_url,
    fetch_exchange_orders,
    oldest_open_order_date,
    parse_naive_utc,
    read_db_orders,
    reconcile,
)

PAIR = "BTC/EUR"


def db_order(order_id="A1", **kw) -> DbOrder:
    base = dict(
        order_id=order_id,
        pair=PAIR,
        side="buy",
        status="closed",
        is_open=False,
        filled=0.001,
        amount=0.001,
        cost=70.0,
        fee_base=0.000004,
    )
    return DbOrder(**{**base, **kw})


def ex_order(order_id="A1", **kw) -> ExchangeOrder:
    base = dict(
        order_id=order_id,
        pair=PAIR,
        side="buy",
        status="closed",
        filled=0.001,
        amount=0.001,
        cost=70.0,
        fee_cost=0.000004,
        fee_currency="BTC",
    )
    return ExchangeOrder(**{**base, **kw})


def kinds(mismatches) -> list[str]:
    return [m.kind for m in mismatches]


def run(db, ex, balance=0.0, open_amount=0.0, **kw):
    return reconcile(db, ex, balance, open_amount, "BTC", **kw)


# --- reconcile --------------------------------------------------------------


def test_clean_match():
    assert run([db_order()], [ex_order()]) == []


def test_missing_db_order():
    out = run([], [ex_order("Z9")])
    assert kinds(out) == ["missing_db_order"]
    assert "Z9" in out[0].detail


def test_amount_mismatch():
    out = run([db_order(filled=0.001)], [ex_order(filled=0.002)])
    assert kinds(out) == ["amount_mismatch"]


def test_fee_mismatch_only_for_base_currency():
    db = [db_order(fee_base=0.0)]
    assert kinds(run(db, [ex_order(fee_cost=0.001)])) == ["fee_mismatch"]
    assert run(db, [ex_order(fee_cost=0.001, fee_currency="EUR")]) == []
    assert run(db, [ex_order(fee_cost=None)]) == []


@pytest.mark.parametrize("status", ["closed", "canceled"])
def test_stale_open_order(status):
    out = run([db_order(is_open=True)], [ex_order(status=status)])
    assert kinds(out) == ["stale_open_order"]


def test_open_db_order_still_open_on_exchange_is_fine():
    assert run([db_order(is_open=True)], [ex_order(status="open")]) == []


def test_dust():
    out = run([], [], balance=0.5, open_amount=0.4)
    assert kinds(out) == ["dust"]
    assert run([], [], balance=None, open_amount=0.4) == []
    assert run([], [], balance=0.4, open_amount=0.4) == []


def test_open_exchange_orders_are_ignored():
    # Not in the DB and not closed: no missing_db_order.
    assert run([], [ex_order("Q1", status="open")]) == []


def test_tolerances():
    db = [db_order(filled=0.001, fee_base=0.0)]
    ex = [ex_order(filled=0.0011, fee_cost=0.0005)]
    assert sorted(kinds(run(db, ex))) == ["amount_mismatch", "fee_mismatch"]
    assert run(db, ex, amount_tolerance=1e-3, fee_tolerance=1e-3) == []
    assert kinds(run([], [], balance=1.0, open_amount=0.0, dust_tolerance=0.5)) == ["dust"]
    assert run([], [], balance=0.4, open_amount=0.0, dust_tolerance=0.5) == []


# --- cursor helpers -----------------------------------------------------------

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
T2 = datetime(2026, 1, 3, tzinfo=UTC)


def test_oldest_open_order_date():
    orders = [
        db_order("a", is_open=True, order_date=T2),
        db_order("b", is_open=True, order_date=T1),
        db_order("c", is_open=False, order_date=T0),
        db_order("d", is_open=True, order_date=None),
    ]
    assert oldest_open_order_date(orders) == T1
    assert oldest_open_order_date([db_order(is_open=False, order_date=T0)]) is None
    assert oldest_open_order_date([]) is None


def test_compute_effective_since():
    assert compute_effective_since(T1, None) == T1
    assert compute_effective_since(T1, T0) == T0
    assert compute_effective_since(T1, T2) == T1


def test_db_path_from_url():
    assert db_path_from_url("sqlite:////abs/path/tradesv3.sqlite") == "/abs/path/tradesv3.sqlite"
    assert db_path_from_url("sqlite:///rel.sqlite") == "rel.sqlite"
    with pytest.raises(ValueError, match="Only sqlite"):
        db_path_from_url("postgresql://user@host/db")
    with pytest.raises(ValueError, match="no path"):
        db_path_from_url("sqlite:///")


def test_parse_naive_utc():
    assert parse_naive_utc("2026-01-02 03:04:05.123456") == datetime(
        2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC
    )
    assert parse_naive_utc("2026-01-02 03:04:05").tzinfo is UTC
    assert parse_naive_utc(None) is None
    assert parse_naive_utc("") is None


# --- fetch_exchange_orders ---------------------------------------------------------


class FakeClient:
    """Scripted pages for fetch_closed_orders; records calls."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        self.calls.append((symbol, since, limit, params))
        return self.pages.pop(0) if self.pages else []


def raw(order_id, symbol=PAIR, **kw):
    return {
        "id": order_id,
        "symbol": symbol,
        "side": "buy",
        "status": "closed",
        "filled": 0.001,
        "amount": 0.001,
        "cost": 70.0,
        "fee": {"cost": 0.000004, "currency": "BTC"},
        **kw,
    }


def test_fetch_completes_on_empty_page_and_filters_by_symbol():
    client = FakeClient([[raw("1"), raw("2", "ETH/EUR")], [raw("3")], []])
    orders = fetch_exchange_orders(client, PAIR, 1234, max_pages=5)
    assert [o.order_id for o in orders] == ["1", "3"]
    assert orders[0].fee_cost == 0.000004 and orders[0].fee_currency == "BTC"
    # Account-wide query, offset advances by the page size, cursor passed through.
    assert [c[3] for c in client.calls] == [{"ofs": 0}, {"ofs": 2}, {"ofs": 3}]
    assert all(c[0] is None and c[1] == 1234 for c in client.calls)


def test_fetch_handles_missing_fee_and_nulls():
    page = [{"id": 7, "symbol": PAIR, "filled": None, "fee": None}]
    (order,) = fetch_exchange_orders(FakeClient([page, []]), PAIR, 0, max_pages=3)
    assert order.order_id == "7"
    assert order.filled == 0.0 and order.fee_cost is None and order.fee_currency is None


def test_fetch_raises_on_repeated_page():
    client = FakeClient([[raw("1")], [raw("1")], []])
    with pytest.raises(IncompleteExchangeHistoryError, match="distinct page"):
        fetch_exchange_orders(client, PAIR, 0, max_pages=5)


def test_fetch_raises_on_page_without_ids():
    with pytest.raises(IncompleteExchangeHistoryError):
        fetch_exchange_orders(FakeClient([[{"symbol": PAIR}]]), PAIR, 0, max_pages=5)


def test_fetch_raises_at_max_pages():
    client = FakeClient([[raw("1")], [raw("2")], [raw("3")]])
    with pytest.raises(IncompleteExchangeHistoryError, match="2-page safety limit"):
        fetch_exchange_orders(client, PAIR, 0, max_pages=2)


@pytest.mark.parametrize("max_pages", [0, -1])
def test_fetch_rejects_non_positive_max_pages(max_pages):
    with pytest.raises(ValueError, match="max_pages"):
        fetch_exchange_orders(FakeClient([]), PAIR, 0, max_pages=max_pages)


# --- read_db_orders ------------------------------------------------------------------


def test_read_db_orders(tmp_path):
    path = tmp_path / "trades.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE orders (
            order_id TEXT, ft_pair TEXT, side TEXT, ft_order_side TEXT, status TEXT,
            ft_is_open INTEGER, filled REAL, amount REAL, ft_amount REAL, cost REAL,
            ft_fee_base REAL, order_date TEXT
        );
        CREATE TABLE trades (pair TEXT, is_open INTEGER, amount REAL);
        INSERT INTO orders VALUES
            ('o1', 'BTC/EUR', 'buy', 'buy', 'closed', 0, 0.001, 0.001, 0.001, 70.0, 4e-6,
             '2026-01-02 03:04:05.000000'),
            ('o2', 'BTC/EUR', NULL, 'sell', 'open', 1, NULL, NULL, 0.002, NULL, NULL, NULL),
            ('o3', 'ETH/EUR', 'buy', 'buy', 'closed', 0, 1.0, 1.0, 1.0, 3000.0, 0.0, NULL),
            ('o4', 'XRP/EUR', 'buy', 'buy', 'closed', 0, 1.0, 1.0, 1.0, 1.0, 0.0, NULL);
        INSERT INTO trades VALUES
            ('BTC/EUR', 1, 0.001), ('BTC/EUR', 1, 0.0005), ('BTC/EUR', 0, 9.0),
            ('ETH/EUR', 0, 5.0), ('XRP/EUR', 1, 100.0);
        """
    )
    con.commit()
    con.close()

    orders, open_amounts = read_db_orders(f"sqlite:///{path}", ["BTC/EUR", "ETH/EUR"])

    by_id = {o.order_id: o for o in orders}
    assert sorted(by_id) == ["o1", "o2", "o3"]
    assert by_id["o1"] == DbOrder(
        order_id="o1",
        pair="BTC/EUR",
        side="buy",
        status="closed",
        is_open=False,
        filled=0.001,
        amount=0.001,
        cost=70.0,
        fee_base=4e-6,
        order_date=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    o2 = by_id["o2"]
    assert o2.is_open is True
    assert o2.side == "sell"  # COALESCE(side, ft_order_side)
    assert o2.filled == 0 and o2.amount == 0.002 and o2.cost == 0 and o2.fee_base == 0
    assert o2.order_date is None
    assert open_amounts == {"BTC/EUR": pytest.approx(0.0015), "ETH/EUR": 0.0}
