"""Read-only daily reconciliation between Freqtrade's trade DB and Kraken history.

Compares, for the bot's whitelisted pair(s):
  - every exchange-closed order maps to a DB order by exchange order id;
  - filled amount and (where the fee currency is the base asset) fee match
    within a precision tolerance;
  - no DB order marked open that the exchange reports closed or canceled;
  - base-asset balance vs. the sum of open-trade amounts (dust).

Opens the SQLite DB read-only (`file:...?mode=ro`). Credentials are read only
from the layered config and are never printed or logged. This module never
references any order-mutating ccxt method (create/cancel/edit order) — see
tests/live/test_reconcile.py for the guard.

Exit code: 0 match, 3 mismatch, 1 error.
"""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import ccxt
from freqtrade.enums import RunMode

from sq.config import load_config
from sq.paths import TRACKED_BASE_CONFIG

DEFAULT_AMOUNT_TOLERANCE = 1e-6
DEFAULT_FEE_TOLERANCE = 1e-6
DEFAULT_DUST_TOLERANCE = 1e-6
DEFAULT_PAGE_SIZE = 200
DEFAULT_MAX_PAGES = 20


# --- Pure computation -------------------------------------------------------


@dataclass
class DbOrder:
    """The fields of one `orders` row needed for reconciliation."""

    order_id: str
    pair: str
    side: str
    status: str | None
    is_open: bool
    filled: float
    amount: float
    cost: float
    fee_base: float
    order_date: datetime | None = None


@dataclass
class ExchangeOrder:
    """The fields of one ccxt order needed for reconciliation."""

    order_id: str
    pair: str
    side: str
    status: str  # ccxt: "open" | "closed" | "canceled" | ...
    filled: float
    amount: float
    cost: float
    fee_cost: float | None
    fee_currency: str | None


@dataclass
class Mismatch:
    kind: str
    detail: str


def reconcile(
    db_orders: list[DbOrder],
    exchange_orders: list[ExchangeOrder],
    base_balance: float | None,
    open_trade_base_amount: float,
    base_currency: str,
    *,
    amount_tolerance: float = DEFAULT_AMOUNT_TOLERANCE,
    fee_tolerance: float = DEFAULT_FEE_TOLERANCE,
    dust_tolerance: float = DEFAULT_DUST_TOLERANCE,
) -> list[Mismatch]:
    """Pure comparison of DB state against exchange-reported state."""
    mismatches: list[Mismatch] = []
    db_by_id = {order.order_id: order for order in db_orders}

    for exchange_order in exchange_orders:
        db_order = db_by_id.get(exchange_order.order_id)
        if exchange_order.status != "closed":
            continue
        if db_order is None:
            mismatches.append(
                Mismatch(
                    "missing_db_order",
                    f"exchange order {exchange_order.order_id} ({exchange_order.pair}) "
                    "filled on Kraken but has no matching DB order",
                )
            )
            continue
        if abs(db_order.filled - exchange_order.filled) > amount_tolerance:
            mismatches.append(
                Mismatch(
                    "amount_mismatch",
                    f"order {exchange_order.order_id}: DB filled {db_order.filled} vs "
                    f"exchange filled {exchange_order.filled}",
                )
            )
        if exchange_order.fee_currency == base_currency and exchange_order.fee_cost is not None:
            if abs(db_order.fee_base - exchange_order.fee_cost) > fee_tolerance:
                mismatches.append(
                    Mismatch(
                        "fee_mismatch",
                        f"order {exchange_order.order_id}: DB fee_base {db_order.fee_base} vs "
                        f"exchange fee {exchange_order.fee_cost} {exchange_order.fee_currency}",
                    )
                )

    exchange_by_id = {order.order_id: order for order in exchange_orders}
    for db_order in db_orders:
        if not db_order.is_open:
            continue
        exchange_order = exchange_by_id.get(db_order.order_id)
        if exchange_order is not None and exchange_order.status in ("closed", "canceled"):
            mismatches.append(
                Mismatch(
                    "stale_open_order",
                    f"DB order {db_order.order_id} is open but exchange reports "
                    f"{exchange_order.status}",
                )
            )

    if base_balance is not None:
        dust = base_balance - open_trade_base_amount
        if abs(dust) > dust_tolerance:
            mismatches.append(
                Mismatch(
                    "dust",
                    f"{base_currency} balance {base_balance} vs sum of open trade amounts "
                    f"{open_trade_base_amount}: dust {dust}",
                )
            )

    return mismatches


def oldest_open_order_date(db_orders: list[DbOrder]) -> datetime | None:
    """Earliest `order_date` among still-open DB orders, or None if there are none."""
    open_dates = [order.order_date for order in db_orders if order.is_open and order.order_date]
    return min(open_dates) if open_dates else None


def compute_effective_since(base_since: datetime, oldest_open: datetime | None) -> datetime:
    """The earlier of the default/explicit `--since` cursor and the oldest open order.

    A daily run being skipped must not let a stale open order fall outside the
    lookback window; the page bound (`--max-pages`/`--page-size`) is unaffected,
    this only moves the pagination's starting cursor further back.
    """
    if oldest_open is not None and oldest_open < base_since:
        return oldest_open
    return base_since


# --- I/O ---------------------------------------------------------------


def db_path_from_url(db_url: str) -> str:
    """Extract a filesystem path from a `sqlite:///...` db_url."""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        raise ValueError(
            f"Only sqlite db_url values are supported for reconciliation, got: {db_url}"
        )
    path = db_url[len(prefix) :]
    if not path:
        raise ValueError(f"db_url has no path: {db_url}")
    return path


def parse_naive_utc(value: str | None) -> datetime | None:
    """Parse a sqlite `orders.order_date` value: stored as a naive UTC timestamp."""
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def read_db_orders(db_url: str, pairs: list[str]) -> tuple[list[DbOrder], dict[str, float]]:
    """Read orders for the given pairs and the open-trade base amount, read-only."""
    path = db_path_from_url(db_url)
    uri = f"file:{path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        placeholders = ",".join("?" for _ in pairs)
        rows = connection.execute(
            f"""
            SELECT order_id, ft_pair, COALESCE(side, ft_order_side), status, ft_is_open,
                   COALESCE(filled, 0), COALESCE(amount, ft_amount), COALESCE(cost, 0),
                   COALESCE(ft_fee_base, 0), order_date
            FROM orders
            WHERE ft_pair IN ({placeholders})
            """,
            pairs,
        ).fetchall()
        db_orders = [
            DbOrder(
                order_id=row[0],
                pair=row[1],
                side=row[2],
                status=row[3],
                is_open=bool(row[4]),
                filled=row[5],
                amount=row[6],
                cost=row[7],
                fee_base=row[8],
                order_date=parse_naive_utc(row[9]),
            )
            for row in rows
        ]

        open_amount_rows = connection.execute(
            f"""
            SELECT pair, COALESCE(SUM(amount), 0)
            FROM trades
            WHERE is_open = 1 AND pair IN ({placeholders})
            GROUP BY pair
            """,
            pairs,
        ).fetchall()
        # Per pair: base amounts of different assets must never be summed together.
        open_trade_base_amounts = {pair: 0.0 for pair in pairs}
        open_trade_base_amounts.update({row[0]: row[1] for row in open_amount_rows})
    finally:
        connection.close()
    return db_orders, open_trade_base_amounts


def fetch_exchange_orders(
    client: ccxt.kraken, pair: str, since_ms: int, page_size: int, max_pages: int
) -> list[ExchangeOrder]:
    """Fetch closed orders for a pair since a cursor, paginated with a bound."""
    orders: list[dict[str, Any]] = []
    cursor = since_ms
    for _ in range(max_pages):
        page = client.fetch_closed_orders(pair, since=cursor, limit=page_size)
        if not page:
            break
        orders.extend(page)
        if len(page) < page_size:
            break
        cursor = page[-1]["timestamp"] + 1
    return [
        ExchangeOrder(
            order_id=str(order["id"]),
            pair=pair,
            side=order.get("side", ""),
            status=order.get("status", ""),
            filled=order.get("filled") or 0.0,
            amount=order.get("amount") or 0.0,
            cost=order.get("cost") or 0.0,
            fee_cost=(order.get("fee") or {}).get("cost"),
            fee_currency=(order.get("fee") or {}).get("currency"),
        )
        for order in orders
    ]


def fetch_base_balance(client: ccxt.kraken, base_currency: str) -> float | None:
    """Read-only total (free + used) balance of the base currency."""
    balance = client.fetch_balance()
    entry = balance.get(base_currency)
    if entry is None:
        return None
    return entry.get("total")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        metavar="PATH",
        help=(
            "Layered config file list, later overrides earlier; may be repeated. "
            "Defaults to the tracked base config. Credentials must come from here."
        ),
    )
    parser.add_argument(
        "--since",
        help="ISO 8601 UTC cursor, e.g. 2026-09-23T00:00:00Z. Defaults to 24 hours ago.",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--amount-tolerance", type=float, default=DEFAULT_AMOUNT_TOLERANCE)
    parser.add_argument("--fee-tolerance", type=float, default=DEFAULT_FEE_TOLERANCE)
    parser.add_argument("--dust-tolerance", type=float, default=DEFAULT_DUST_TOLERANCE)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    config_paths = args.configs or [TRACKED_BASE_CONFIG]
    config = load_config(config_paths, RunMode.UTIL_NO_EXCHANGE)

    exchange_cfg = config.get("exchange", {})
    key = exchange_cfg.get("key") or ""
    secret = exchange_cfg.get("secret") or ""
    if not key or not secret:
        raise ValueError(
            "Reconciliation needs read-only exchange credentials from a layered config "
            "(e.g. config/local/secrets.json); none were found in the merged config."
        )

    pairs = exchange_cfg.get("pair_whitelist") or []
    if not pairs:
        raise ValueError("No pairs in the merged config's exchange.pair_whitelist")

    base_since = (
        datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if args.since
        else datetime.now(UTC) - timedelta(hours=24)
    )

    db_orders, open_trade_base_amounts = read_db_orders(config["db_url"], pairs)

    # A skipped daily run must not push a still-open order outside the lookback
    # window: widen the cursor to the oldest open DB order if it predates
    # base_since. The page bound (--page-size/--max-pages) is unchanged.
    since = compute_effective_since(base_since, oldest_open_order_date(db_orders))
    since_ms = int(since.timestamp() * 1000)

    client = ccxt.kraken({"enableRateLimit": True, "apiKey": key, "secret": secret})

    all_mismatches: list[Mismatch] = []
    for pair in pairs:
        base_currency = pair.split("/")[0]
        exchange_orders = fetch_exchange_orders(
            client, pair, since_ms, args.page_size, args.max_pages
        )
        base_balance = fetch_base_balance(client, base_currency)
        pair_db_orders = [order for order in db_orders if order.pair == pair]
        mismatches = reconcile(
            pair_db_orders,
            exchange_orders,
            base_balance,
            open_trade_base_amounts[pair],
            base_currency,
            amount_tolerance=args.amount_tolerance,
            fee_tolerance=args.fee_tolerance,
            dust_tolerance=args.dust_tolerance,
        )
        all_mismatches.extend(mismatches)

    report = {
        "pairs": pairs,
        "since": since.isoformat(),
        "match": not all_mismatches,
        "mismatches": [asdict(mismatch) for mismatch in all_mismatches],
    }
    print(json.dumps(report, indent=2))
    return 0 if not all_mismatches else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
        # Never print str(exc): it can echo config paths, pair/account details or,
        # depending on the failure, credential-adjacent text from ccxt. Match the
        # inner handlers in preflight.py: name the exception class only.
        print(json.dumps({"error": f"{exc.__class__.__name__}: reconciliation failed"}, indent=2))
        sys.exit(1)
