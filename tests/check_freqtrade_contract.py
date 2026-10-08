"""Pinned-image integration check. Run directly, never through the local stub.

Creates only temporary data, exercises the real Freqtrade ORM schema and
strategy resolver, then runs the reporting CLI against synthetic closed candles.
No exchange connection or order submission.
"""

import contextlib
import io
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd
from freqtrade.enums import RunMode
from freqtrade.persistence import LocalTrade, Order, PairLocks, Trade
from freqtrade.plugins.protections.max_drawdown_protection import MaxDrawdown
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sq.config import TRACKED_BASE_CONFIG, load_config, load_strategy
from sq.live import forward
from sq.research import breakout


def check_pairlock_boundaries():
    """Keep pure research replay's unlock boundaries tied to the pinned runtime."""
    PairLocks.use_db = False
    PairLocks.timeframe = "4h"
    closed = pd.Timestamp("2030-01-01", tz="UTC").to_pydatetime()
    try:
        for duration in (1, 42):
            PairLocks.reset_locks()
            nominal_end = closed + timedelta(hours=4 * duration)
            PairLocks.lock_pair("BTC/EUR", nominal_end, now=closed, side="long")
            assert PairLocks.is_pair_locked("BTC/EUR", nominal_end, "long")
            assert not PairLocks.is_pair_locked("BTC/EUR", nominal_end + timedelta(hours=4), "long")
    finally:
        PairLocks.reset_locks()
        PairLocks.use_db = True


def check_max_drawdown_contract():
    """Compare the replay directly with the pinned protection and pair-lock rounding."""
    now = pd.Timestamp("2030-06-01", tz="UTC")
    cases = [
        ([], []),
        ([0], [-0.25]),
        ([0], [-0.2501]),
        ([1, 0], [10.0, -0.3]),
        ([2, 1, 0], [-0.15, -0.15, 0.5]),
        ([540, 0], [-0.2, -0.1]),
        ([539, 0], [-0.2, -0.1]),
    ]
    # Historical train drawdown: three signal losses trigger a lock even
    # though no entry occurs during that lock and all recorded trades match.
    cases.append(([525, 378, 0], [-0.08868, -0.12978, -0.15514]))
    rules = breakout.H1_RULES
    protection = MaxDrawdown(
        {"timeframe": "4h"},
        {
            "lookback_period_candles": rules.drawdown_lookback,
            "trade_limit": rules.drawdown_trade_limit,
            "max_allowed_drawdown": rules.drawdown_limit,
            "stop_duration_candles": rules.drawdown_duration,
        },
    )
    saved_trades, saved_use_db = LocalTrade.bt_trades, Trade.use_db
    PairLocks.use_db = False
    PairLocks.timeframe = "4h"
    Trade.use_db = False
    try:
        for ages, profits in cases:
            closed = [
                (now - age * breakout.STEP, profit)
                for age, profit in zip(ages, profits, strict=True)
            ]
            LocalTrade.bt_trades = [
                LocalTrade(
                    pair="BTC/EUR",
                    is_open=False,
                    close_date=date.to_pydatetime(),
                    close_profit=profit,
                    exit_reason="exit_signal",
                )
                for date, profit in closed
            ]
            expected = protection.global_stop(now.to_pydatetime(), "long", 1000.0)
            actual = breakout.max_drawdown_lock_end(closed, now)
            if expected is None:
                assert actual is None, (ages, profits, actual)
            else:
                PairLocks.reset_locks()
                lock = PairLocks.lock_pair("*", expected.until, now=now.to_pydatetime())
                assert actual == pd.Timestamp(lock.lock_end_time), (ages, profits, actual, lock)
    finally:
        LocalTrade.bt_trades, Trade.use_db = saved_trades, saved_use_db
        PairLocks.reset_locks()
        PairLocks.use_db = True


def check():
    check_pairlock_boundaries()
    check_max_drawdown_contract()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db = root / "trades.sqlite"
        engine = create_engine(f"sqlite:///{db}")
        Trade.metadata.create_all(engine)
        step = pd.Timedelta(hours=4)
        start = pd.Timestamp.now(tz="UTC").floor(step) - 200 * step
        submitted = (start + 130 * step).to_pydatetime().replace(tzinfo=None)
        filled = submitted + timedelta(minutes=2)
        closed = submitted + 5 * timedelta(hours=4, minutes=0)
        trade = Trade(
            exchange="kraken",
            pair="BTC/EUR",
            is_open=False,
            open_date=submitted,
            close_date=closed,
            open_rate=100.0,
            close_rate=100.0,
            amount=0.08,
            stake_amount=8.0,
            fee_open=0.004,
            fee_close=0.004,
            close_profit_abs=-0.064,
            close_profit=-0.008,
            strategy="H1ChannelBreakout",
            timeframe=240,
            amount_precision=0.00000001,
            precision_mode=4,
            exit_reason="exit_signal",
        )
        for index, side, date in [(1, "buy", filled), (2, "sell", closed)]:
            trade.orders.append(
                Order(
                    order_id=str(index),
                    ft_order_side=side,
                    ft_pair="BTC/EUR",
                    ft_is_open=False,
                    status="closed",
                    filled=0.08,
                    amount=0.08,
                    remaining=0.0,
                    average=100.0,
                    price=100.0,
                    ft_amount=0.08,
                    ft_price=100.0,
                    order_date=submitted if side == "buy" else closed,
                    order_filled_date=date,
                    ft_fee_base=0.0,
                )
            )
        with Session(engine) as session:
            session.add(trade)
            session.commit()
        snapshot, _ = forward.filled_snapshot(str(db), ["BTC/EUR"])
        assert len(snapshot) == 1 and snapshot[0].open_date.minute == 2
        candles = root / "candles.json"
        candles.write_text(
            json.dumps(
                [
                    [int((start + i * step).timestamp() * 1000), 100.0, 100.0, 100.0, 100.0, 1.0]
                    for i in range(200)
                ]
            )
        )
        overlay = root / "config.json"
        overlay.write_text(json.dumps({"db_url": f"sqlite:///{db}"}))
        output = root / "reports/result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            result = forward.main(
                [
                    "--config",
                    str(TRACKED_BASE_CONFIG),
                    "--config",
                    str(overlay),
                    "--candles",
                    str(candles),
                    "--out",
                    str(output),
                ]
            )
        report = json.loads(output.read_text())
        assert result == 4  # synthetic trade has no breakout: F1 must stop it
        assert report["performance"]["closed_trades"]["count"] == 1
        assert report["execution"]["trades"][0]["entry_delay_minutes"] == 2.0
        assert report["account"]["notional"] == 10.0
        assert report["h2_paired"]["verdict"] == "INCONCLUSIVE"

        # Resolve research-only H2 with the real IStrategy and test the callback
        # against a data provider whose backtest frame includes the entry row.
        sys.path.insert(0, "/freqtrade/strategies")
        config = load_config([TRACKED_BASE_CONFIG], RunMode.UTIL_NO_EXCHANGE)
        config.update(
            strategy="H2VolTargetBreakout", strategy_path="/workspace/research/strategies"
        )
        strategy = load_strategy(config)
        signal = start + step
        frame = pd.DataFrame(
            {"date": [start, signal, signal + step], "h2_stake_fraction": [0.9, 0.5, 0.25]}
        )

        class Provider:
            def get_analyzed_dataframe(self, pair, timeframe):
                return frame, None

        strategy.dp = Provider()
        stake = strategy.custom_stake_amount(
            "BTC/EUR", signal + step, 100.0, 1000.0, None, 1000.0, 1.0, None, "long"
        )
        assert stake == 500.0, stake
        engine.dispose()
    print("Pinned Freqtrade locks, MaxDrawdown, ORM, forward CLI and H2 callback: PASS")


if __name__ == "__main__":
    check()
