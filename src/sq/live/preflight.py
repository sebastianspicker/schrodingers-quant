"""Read-only feasibility check for a live Kraken entry, before any order exists.

Computes whether a (pair, stake, stoploss) profile can enter and — after
precision rounding, entry fees and dust — still exit at the stop price. Uses
only ccxt's public Kraken market data (`load_markets`, `fetch_order_book`).
If credentials are present in the merged config, also reads the account's
fee tier (`fetch_trading_fee`) and EUR balance (`fetch_balance`); both are
read-only calls. This module never references any order-mutating ccxt method
(create/cancel/edit order).

Exit code: 0 feasible, 2 infeasible, 1 on error (including invalid arguments).
"""

import argparse
import json
import math
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sq.config import TRACKED_BASE_CONFIG, load_config, load_strategy
from sq.live import CliArgumentParser, SafeMessageError, cli_error

# ccxt and Freqtrade are imported inside the I/O functions, so the pure
# feasibility computation is importable and unit-testable without the image.

# --- Pure computation ------------------------------------------------------


@dataclass
class MarketProfile:
    """Market facts a preflight check needs, extracted from ccxt structures."""

    pair: str
    price: float  # top-of-book ask: a marketable buy pays this
    bid: float
    ask: float
    amount_min: float | None
    cost_min: float | None
    amount_precision: float | None
    price_precision: float | None
    precision_mode: int
    taker_fee: float
    fee_source: str
    account_fee_verified: bool
    stoploss_on_exchange_supported: bool
    eur_balance: float | None
    eur_balance_source: str


# Kraken Pro base-tier taker fee, also the fee assumed by research/hypotheses/H1.md.
CONSERVATIVE_TAKER_FEE = 0.004


def compute_spread_bps(bid: float, ask: float) -> float:
    """Bid/ask spread in basis points of the mid price."""
    mid = (bid + ask) / 2
    if mid <= 0:
        return float("inf")
    return (ask - bid) / mid * 10_000


@dataclass
class FeasibilityReport:
    """Result of assessing whether a stake can enter and exit a stop-out."""

    entry_amount_requested: float
    entry_amount: float
    entry_cost_eur: float
    stake_dust_eur: float
    entry_fee_base: float
    amount_after_entry_fee: float
    sellable_amount: float
    dust_base: float
    stop_price: float
    exit_value_at_stop_eur: float
    exit_fee_quote_eur: float
    max_loss_eur: float
    feasible: bool
    reasons: list[str]


AmountRounder = Callable[[float, float | None, int], float]


def validate_stoploss(stoploss: float) -> float:
    """A stoploss is a loss fraction strictly between -1 and 0 (e.g. -0.20).

    +0.2 or -1.5 would otherwise yield a "feasible" report with a stop price
    above the entry or at/below zero.
    """
    stoploss = float(stoploss)
    if not (-1 < stoploss < 0):
        raise SafeMessageError(
            f"stoploss must be a negative fraction greater than -1 (e.g. -0.20), got {stoploss}"
        )
    return stoploss


def freqtrade_amount_rounder() -> AmountRounder:
    """Freqtrade's `amount_to_precision` (ccxt TRUNCATE semantics), imported lazily."""
    from freqtrade.exchange.exchange_utils import amount_to_precision

    return amount_to_precision


def assess_feasibility(
    market: MarketProfile,
    stake_eur: float,
    stoploss: float,
    *,
    require_account_data: bool = False,
    round_amount: AmountRounder | None = None,
) -> FeasibilityReport:
    """Pure feasibility computation for one (pair, stake, stoploss) profile.

    Assumes Kraken's default convention: the entry (buy) fee is charged in the
    base currency, deducted from the amount received. Amounts are always
    rounded DOWN to exchange precision, matching what an order can actually
    request or a wallet can actually hold and later sell. `round_amount`
    defaults to Freqtrade's `amount_to_precision`; tests inject their own.
    """
    stoploss = validate_stoploss(stoploss)
    raw_stake = stake_eur
    try:
        stake_eur = float(stake_eur)
    except (TypeError, ValueError):
        stake_eur = math.nan
    # NaN compares False against every minimum below, so it would report "feasible".
    if not (math.isfinite(stake_eur) and stake_eur > 0):
        raise SafeMessageError(f"stake must be a positive finite EUR amount, got {raw_stake!r}")
    amount_to_precision = round_amount or freqtrade_amount_rounder()
    reasons: list[str] = []

    if require_account_data and not market.account_fee_verified:
        reasons.append("account fee tier was not verified")
    if require_account_data and market.eur_balance is None:
        reasons.append("account quote-currency balance was not read")

    entry_amount_requested = stake_eur / market.price
    entry_amount = amount_to_precision(
        entry_amount_requested, market.amount_precision, market.precision_mode
    )
    entry_cost_eur = entry_amount * market.price
    stake_dust_eur = stake_eur - entry_cost_eur

    if entry_amount <= 0:
        reasons.append("entry amount rounds down to zero at this precision")
    elif market.amount_min is not None and entry_amount < market.amount_min:
        reasons.append(f"entry amount {entry_amount} is below exchange minimum {market.amount_min}")
    if market.cost_min is not None and entry_cost_eur < market.cost_min:
        reasons.append(
            f"entry cost {entry_cost_eur} EUR is below exchange minimum {market.cost_min}"
        )

    entry_fee_base = entry_amount * market.taker_fee
    amount_after_entry_fee = entry_amount - entry_fee_base
    sellable_amount = amount_to_precision(
        amount_after_entry_fee, market.amount_precision, market.precision_mode
    )
    dust_base = amount_after_entry_fee - sellable_amount

    if market.amount_min is not None and sellable_amount < market.amount_min:
        reasons.append(
            f"sellable amount after entry fee {sellable_amount} is below "
            f"exchange minimum {market.amount_min}: the exit could be rejected"
        )

    stop_price = market.price * (1 + stoploss)
    exit_value_at_stop_eur = sellable_amount * stop_price
    if market.cost_min is not None and exit_value_at_stop_eur < market.cost_min:
        reasons.append(
            f"exit value at the stop price {exit_value_at_stop_eur} EUR is below "
            f"exchange minimum {market.cost_min}: a full stop-out could be rejected"
        )

    if market.eur_balance is not None and market.eur_balance < stake_eur:
        reasons.append(
            f"account EUR balance {market.eur_balance} is less than the requested stake {stake_eur}"
        )

    exit_fee_quote_eur = exit_value_at_stop_eur * market.taker_fee
    exit_proceeds_eur = exit_value_at_stop_eur - exit_fee_quote_eur
    max_loss_eur = entry_cost_eur - exit_proceeds_eur

    return FeasibilityReport(
        entry_amount_requested=entry_amount_requested,
        entry_amount=entry_amount,
        entry_cost_eur=entry_cost_eur,
        stake_dust_eur=stake_dust_eur,
        entry_fee_base=entry_fee_base,
        amount_after_entry_fee=amount_after_entry_fee,
        sellable_amount=sellable_amount,
        dust_base=dust_base,
        stop_price=stop_price,
        exit_value_at_stop_eur=exit_value_at_stop_eur,
        exit_fee_quote_eur=exit_fee_quote_eur,
        max_loss_eur=max_loss_eur,
        feasible=not reasons,
        reasons=reasons,
    )


# --- I/O ---------------------------------------------------------------


def fetch_market_profile(pair: str, config: dict) -> MarketProfile:
    """Read-only ccxt calls: public market data, plus account data if credentials exist.

    Never calls an order-mutating ccxt method.
    """
    import ccxt
    from freqtrade.exchange.kraken import Kraken

    exchange_cfg = config.get("exchange", {})
    key = exchange_cfg.get("key") or ""
    secret = exchange_cfg.get("secret") or ""
    has_credentials = bool(key) and bool(secret)

    ccxt_kwargs: dict[str, Any] = {"enableRateLimit": True}
    if has_credentials:
        ccxt_kwargs.update({"apiKey": key, "secret": secret})
    client = ccxt.kraken(ccxt_kwargs)

    markets = client.load_markets()
    if pair not in markets:
        raise SafeMessageError(f"{pair} is not a Kraken market")
    market = markets[pair]

    order_book = client.fetch_order_book(pair, limit=5)
    if not order_book["bids"] or not order_book["asks"]:
        raise SafeMessageError(f"{pair} order book is empty")
    bid = order_book["bids"][0][0]
    ask = order_book["asks"][0][0]

    # ccxt's bundled Kraken default (0.26 %) predates Kraken Pro's base-tier taker
    # fee; without an account fee tier, assume the higher, conservative value.
    market_taker_fee = market.get("taker") or 0.0
    taker_fee = max(market_taker_fee, CONSERVATIVE_TAKER_FEE)

    def fallback_fee_source(why: str) -> str:
        # Name whichever side of max(market default, conservative) is in use.
        if market_taker_fee > CONSERVATIVE_TAKER_FEE:
            return (
                f"ccxt market default taker fee {market_taker_fee} ({why}; above the "
                f"conservative base-tier assumption {CONSERVATIVE_TAKER_FEE})"
            )
        return (
            f"conservative base-tier assumption {CONSERVATIVE_TAKER_FEE} ({why}; "
            f"ccxt market default {market_taker_fee})"
        )

    fee_source = fallback_fee_source("no credentials")
    account_fee_verified = False

    eur_balance: float | None = None
    eur_balance_source = "not read (no credentials)"

    if has_credentials:
        # Kraken supports the per-symbol `fetchTradingFee`, not the bulk
        # `fetchTradingFees` ccxt capability; use what Kraken actually offers.
        try:
            fee_info = client.fetch_trading_fee(pair)
            pair_taker_fee = fee_info.get("taker")
            if pair_taker_fee is not None:
                taker_fee = pair_taker_fee
                fee_source = "account fee tier (fetch_trading_fee)"
                account_fee_verified = True
            else:
                fee_source = fallback_fee_source("fetch_trading_fee had no taker field")
        except Exception as exc:
            fee_source = fallback_fee_source(f"fetch_trading_fee failed: {exc.__class__.__name__}")

        try:
            balance = client.fetch_balance()
            quote = market["quote"]
            entry = balance.get(quote)
            if entry is None:
                # ccxt commonly omits zero-balance currencies (as reconcile's
                # fetch_base_balance handles too): an unfunded key reads as 0.0.
                eur_balance = 0.0
                eur_balance_source = f"account balance (fetch_balance; no {quote} entry, 0.0)"
            else:
                eur_balance = entry.get("free")
                eur_balance_source = "account balance (fetch_balance)"
        except Exception as exc:
            eur_balance_source = f"not read (fetch_balance failed: {exc.__class__.__name__})"

    return MarketProfile(
        pair=pair,
        price=ask,
        bid=bid,
        ask=ask,
        amount_min=market.get("limits", {}).get("amount", {}).get("min"),
        cost_min=market.get("limits", {}).get("cost", {}).get("min"),
        amount_precision=market.get("precision", {}).get("amount"),
        price_precision=market.get("precision", {}).get("price"),
        precision_mode=client.precisionMode,
        taker_fee=taker_fee,
        fee_source=fee_source,
        account_fee_verified=account_fee_verified,
        stoploss_on_exchange_supported=bool(Kraken._ft_has.get("stoploss_on_exchange", False)),
        eur_balance=eur_balance,
        eur_balance_source=eur_balance_source,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = CliArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        metavar="PATH",
        help=(
            "Layered config file list, later overrides earlier; may be repeated. "
            "Defaults to the tracked base config."
        ),
    )
    parser.add_argument(
        "--pair",
        help="Trading pair, e.g. BTC/EUR. Defaults to the config's first whitelisted pair.",
    )
    parser.add_argument(
        "--stake", type=float, help="Stake in EUR. Defaults to the config's stake_amount."
    )
    parser.add_argument(
        "--stoploss",
        type=float,
        help=(
            "Stoploss as a negative fraction, e.g. -0.20. Defaults to the configured "
            "strategy's stoploss."
        ),
    )
    parser.add_argument(
        "--require-account-data",
        action="store_true",
        help=(
            "Report infeasible unless authenticated fee-tier and quote-balance reads both "
            "succeed; required before the live pilot."
        ),
    )
    return parser


def main() -> int:
    from freqtrade.enums import RunMode

    args = build_arg_parser().parse_args()
    config_paths = args.configs or [TRACKED_BASE_CONFIG]
    config = load_config(config_paths, RunMode.UTIL_NO_EXCHANGE)

    pair = args.pair or (config.get("exchange", {}).get("pair_whitelist") or [None])[0]
    if not pair:
        raise SafeMessageError("No --pair given and no pair_whitelist entry in the merged config")

    stake_eur = args.stake if args.stake is not None else config.get("stake_amount")
    if stake_eur is None:
        raise SafeMessageError("No --stake given and no stake_amount in the merged config")

    if args.stoploss is not None:
        stoploss = args.stoploss
    else:
        # The stoploss lives in the strategy class, not in any config layer.
        # Load it the way the bot does (Freqtrade's resolver, which also applies
        # a config override if one exists), so preflight cannot drift from it.
        if not config.get("strategy"):
            raise SafeMessageError("No --stoploss given and no strategy in the merged config")
        stoploss = load_strategy(config).stoploss
    stoploss = validate_stoploss(stoploss)

    market = fetch_market_profile(pair, config)
    feasibility = assess_feasibility(
        market,
        stake_eur,
        float(stoploss),
        require_account_data=args.require_account_data,
    )

    report = {
        "pair": pair,
        "stake_eur": stake_eur,
        "stoploss": stoploss,
        "price": market.price,
        "bid": market.bid,
        "ask": market.ask,
        "spread_bps": compute_spread_bps(market.bid, market.ask),
        "amount_min": market.amount_min,
        "cost_min": market.cost_min,
        "amount_precision": market.amount_precision,
        "price_precision": market.price_precision,
        "precision_mode": market.precision_mode,
        "taker_fee": market.taker_fee,
        "fee_source": market.fee_source,
        "account_fee_verified": market.account_fee_verified,
        "eur_balance": market.eur_balance,
        "eur_balance_source": market.eur_balance_source,
        "stoploss_on_exchange_supported": market.stoploss_on_exchange_supported,
        **asdict(feasibility),
    }
    print(json.dumps(report, indent=2, sort_keys=False))
    return 0 if feasibility.feasible else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - top-level CLI error boundary
        # Deliberate SafeMessageErrors print their message; anything else only
        # its class name (see sq.live.cli_error).
        print(json.dumps({"error": cli_error(exc, "preflight")}, indent=2))
        sys.exit(1)
