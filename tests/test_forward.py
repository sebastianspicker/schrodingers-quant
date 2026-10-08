"""Tests of the forward paper-trading report (sq.live.forward): pure functions
on synthetic candles and a synthetic Freqtrade trade database."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from conftest import make_candles

from H1ChannelBreakout import H1ChannelBreakout
from sq.live import forward

STEP = pd.Timedelta(hours=4)


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def paper_trade(**overrides) -> forward.PaperTrade:
    base = {
        "id": 1,
        "pair": "BTC/EUR",
        "is_open": False,
        "open_date": ts("2025-01-05 00:00:30"),
        "close_date": ts("2025-01-07 00:00:20"),
        "open_rate": 100.0,
        "close_rate": 110.0,
        "amount": 0.08,
        "stake_amount": 8.0,
        "fee_open": 0.004,
        "fee_close": 0.004,
        "profit_abs": 0.7355,
        "profit_ratio": 0.0919,
        "exit_reason": "exit_signal",
        "strategy": "H1ChannelBreakout",
        "timeframe": "4h",
    }
    base.update(overrides)
    return forward.PaperTrade(**base)


# --- database -----------------------------------------------------------------


def create_db(path: Path, trades: list[dict], orders: list[dict]) -> str:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE trades (id INTEGER PRIMARY KEY, pair TEXT, is_open INTEGER,
        open_date TEXT, close_date TEXT, open_rate REAL, close_rate REAL, amount REAL,
        stake_amount REAL, fee_open REAL, fee_close REAL, close_profit_abs REAL,
        close_profit REAL, exit_reason TEXT, strategy TEXT, timeframe TEXT)"""
    )
    connection.execute(
        "CREATE TABLE orders (ft_pair TEXT, ft_order_side TEXT, status TEXT, order_id TEXT)"
    )
    for t in trades:
        connection.execute(
            "INSERT INTO trades VALUES (:id, :pair, :is_open, :open_date, :close_date, "
            ":open_rate, :close_rate, :amount, :stake_amount, :fee_open, :fee_close, "
            ":close_profit_abs, :close_profit, :exit_reason, :strategy, :timeframe)",
            t,
        )
    for o in orders:
        connection.execute(
            "INSERT INTO orders VALUES (:ft_pair, :ft_order_side, :status, :order_id)", o
        )
    connection.commit()
    connection.close()
    return str(path)


def test_read_paper_trades_and_order_counts(tmp_path: Path) -> None:
    db = create_db(
        tmp_path / "dry-run.sqlite",
        [
            {
                "id": 1,
                "pair": "BTC/EUR",
                "is_open": 0,
                "open_date": "2025-01-05 00:00:30.123456",
                "close_date": "2025-01-07 00:00:20",
                "open_rate": 100.0,
                "close_rate": 110.0,
                "amount": 0.08,
                "stake_amount": 8.0,
                "fee_open": 0.004,
                "fee_close": 0.004,
                "close_profit_abs": 0.7355,
                "close_profit": 0.0919,
                "exit_reason": "exit_signal",
                "strategy": "H1ChannelBreakout",
                "timeframe": "4h",
            },
            {
                "id": 2,
                "pair": "BTC/EUR",
                "is_open": 1,
                "open_date": "2025-01-09 00:00:10",
                "close_date": None,
                "open_rate": 120.0,
                "close_rate": None,
                "amount": 0.07,
                "stake_amount": 8.0,
                "fee_open": 0.004,
                "fee_close": None,
                "close_profit_abs": None,
                "close_profit": None,
                "exit_reason": None,
                "strategy": "H1ChannelBreakout",
                "timeframe": "4h",
            },
            {
                "id": 3,
                "pair": "ETH/EUR",
                "is_open": 0,
                "open_date": "2025-01-01 00:00:00",
                "close_date": "2025-01-02 00:00:00",
                "open_rate": 1.0,
                "close_rate": 1.0,
                "amount": 1.0,
                "stake_amount": 8.0,
                "fee_open": 0.004,
                "fee_close": 0.004,
                "close_profit_abs": 0.0,
                "close_profit": 0.0,
                "exit_reason": "exit_signal",
                "strategy": "H1ChannelBreakout",
                "timeframe": "4h",
            },
        ],
        [
            {"ft_pair": "BTC/EUR", "ft_order_side": "entry", "status": "closed", "order_id": "a"},
            {"ft_pair": "BTC/EUR", "ft_order_side": "entry", "status": "canceled", "order_id": "b"},
            {"ft_pair": "BTC/EUR", "ft_order_side": "exit", "status": "closed", "order_id": "c"},
            {"ft_pair": "ETH/EUR", "ft_order_side": "entry", "status": "closed", "order_id": "d"},
        ],
    )
    trades = forward.read_paper_trades(db, ["BTC/EUR"])
    assert [t.id for t in trades] == [1, 2]
    assert trades[0].open_date == ts("2025-01-05 00:00:30.123456")
    assert trades[0].close_date == ts("2025-01-07 00:00:20")
    assert trades[1].is_open and trades[1].close_date is None and trades[1].fee_close is None
    assert forward.read_order_counts(db, ["BTC/EUR"]) == {
        "entry:canceled": 1,
        "entry:closed": 1,
        "exit:closed": 1,
    }


def test_read_only_connection_does_not_create_a_database(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        forward.read_paper_trades(str(missing), ["BTC/EUR"])
    assert not missing.exists()


def test_db_path_from_url() -> None:
    assert forward.db_path_from_url("sqlite:////freqtrade/x.sqlite") == "/freqtrade/x.sqlite"
    with pytest.raises(ValueError):
        forward.db_path_from_url("postgresql://x")
    with pytest.raises(ValueError):
        forward.db_path_from_url("sqlite:///")


# --- candles ------------------------------------------------------------------------


def test_candles_from_ohlcv_sorts_and_dedupes() -> None:
    rows = [
        [1_735_689_600_000, 1, 2, 0.5, 1.5, 10],  # 2025-01-01 00:00 UTC
        [1_735_675_200_000, 1, 2, 0.5, 1.2, 10],  # 2024-12-31 20:00 UTC
        [1_735_689_600_000, 1, 2, 0.5, 1.5, 10],
    ]
    frame = forward.candles_from_ohlcv(rows)
    assert list(frame["date"]) == [ts("2024-12-31 20:00"), ts("2025-01-01 00:00")]
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_load_candles_file_json(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps([[1_735_689_600_000, 1, 2, 0.5, 1.5, 10]]))
    assert forward.load_candles_file(path)["close"].iloc[0] == 1.5


def test_timeframe_to_timedelta() -> None:
    assert forward.timeframe_to_timedelta("4h") == pd.Timedelta(hours=4)
    assert forward.timeframe_to_timedelta("15m") == pd.Timedelta(minutes=15)
    assert forward.timeframe_to_timedelta("1d") == pd.Timedelta(days=1)


# --- execution quality ----------------------------------------------------------


def test_execution_of_measures_slippage_fees_and_delay() -> None:
    candles = make_candles([100.0] * 60, start="2025-01-01")
    # Candle containing the entry (2025-01-05 00:00) opens at the previous close, 100.
    trade = paper_trade(open_rate=100.5, close_rate=99.0)
    execution = forward.execution_of(trade, candles, STEP)
    assert execution.entry_reference_open == 100.0
    assert execution.entry_slippage_bps == pytest.approx(50.0)
    assert execution.entry_delay_minutes == pytest.approx(0.5)
    assert execution.exit_reference_open == 100.0
    # Received 99 against reference 100: 100 basis points of the reference.
    assert execution.exit_slippage_bps == pytest.approx((1 - 99 / 100) * 10_000, abs=0.01)
    assert execution.fee_open_bps == 40.0 and execution.fee_close_bps == 40.0
    assert execution.round_trip_cost_pct == pytest.approx(
        (50 + (1 - 99 / 100) * 10_000 + 80) / 100, abs=1e-3
    )


def test_execution_of_open_trade_has_no_exit_figures() -> None:
    candles = make_candles([100.0] * 60, start="2025-01-01")
    trade = paper_trade(is_open=True, close_date=None, close_rate=None, fee_close=None)
    execution = forward.execution_of(trade, candles, STEP)
    assert execution.exit_slippage_bps is None and execution.round_trip_cost_pct is None


def test_execution_report_summary() -> None:
    candles = make_candles([100.0] * 60, start="2025-01-01")
    trades = [
        paper_trade(id=1, open_rate=100.0, close_rate=100.0),
        paper_trade(id=2, open_rate=101.0, close_rate=100.0),
    ]
    report = forward.execution_report(trades, candles, STEP)
    summary = report["summary"]
    assert summary["closed_round_trips_measured"] == 2
    assert summary["assumed_round_trip_cost_pct"] == pytest.approx(1.0)
    assert summary["stress_round_trip_cost_pct"] == pytest.approx(2.0)
    assert summary["round_trip_cost_pct_mean"] == pytest.approx((0.8 + 1.8) / 2)
    assert len(report["trades"]) == 2


# --- signal fidelity ------------------------------------------------------------


def test_compute_signals_with_h1_on_a_breakout(candles_factory) -> None:
    closes = [100.0] * 130 + [120.0] + [120.0] * 10
    candles = candles_factory(closes, start="2025-01-01")
    frame = forward.compute_signals(H1ChannelBreakout({}), candles, "BTC/EUR")
    entries = forward.signal_times(frame, "enter_long")
    assert entries == [candles["date"].iloc[130]]
    assert forward.signal_times(frame, "exit_long") == []


def test_signal_fidelity_matches_explains_and_flags() -> None:
    start = ts("2025-01-01")
    end = ts("2025-02-01")
    s1, s2, s3, s4 = (
        ts("2025-01-05 00:00"),
        ts("2025-01-06 00:00"),
        ts("2025-01-10 00:00"),
        ts("2025-01-31 20:00"),
    )
    trades = [
        # Acts on s1: fills within the next candle.
        paper_trade(
            id=1,
            open_date=s1 + STEP + pd.Timedelta(seconds=5),
            close_date=ts("2025-01-08 00:00:05"),
        ),
        # No signal anywhere near: a defect.
        paper_trade(
            id=2, open_date=ts("2025-01-20 00:00:05"), close_date=ts("2025-01-21 00:00:05")
        ),
    ]
    result = forward.signal_fidelity(
        [s1, s2, s3, s4], trades, STEP, window_start=start, window_end=end
    )
    assert result["entries_matched"] == 1
    assert [e["trade_id"] for e in result["entries_without_signal"]] == [2]
    reasons = {e["signal_candle"]: e["reason"] for e in result["signals_without_entry_explained"]}
    assert reasons[s2.isoformat()] == "in position or cooldown"  # trade 1 was open
    assert reasons[s4.isoformat()] == "entry window still open"
    assert [e["signal_candle"] for e in result["signals_without_entry_unexplained"]] == [
        s3.isoformat()
    ]


def test_signal_fidelity_unverifiable_before_startup_candles() -> None:
    start, end = ts("2025-01-01"), ts("2025-02-01")
    valid_from = ts("2025-01-21")
    trades = [paper_trade(id=7, open_date=ts("2025-01-10 00:00:05"), close_date=ts("2025-01-12"))]
    result = forward.signal_fidelity(
        [], trades, STEP, window_start=start, window_end=end, signals_valid_from=valid_from
    )
    assert [e["trade_id"] for e in result["entries_unverifiable"]] == [7]
    assert result["entries_without_signal"] == []


# --- performance --------------------------------------------------------------


def test_performance_report_marks_to_market_on_the_same_basis_as_the_backtest() -> None:
    closes = [100.0] * 24 + [110.0] * 12 + [120.0] * 24  # 2025-01-01 .. 2025-01-10 20:00
    candles = make_candles(closes, start="2025-01-01")
    closed = paper_trade(
        id=1,
        open_date=ts("2025-01-05 00:00:05"),
        close_date=ts("2025-01-07 00:00:05"),
        open_rate=100.0,
        close_rate=110.0,
        amount=0.08,
        fee_open=0.004,
        fee_close=0.004,
        profit_abs=0.08 * 110 * 0.996 - 0.08 * 100 * 1.004,
        profit_ratio=(0.08 * 110 * 0.996 - 0.08 * 100 * 1.004) / 8.0,
    )
    open_trade = paper_trade(
        id=2,
        is_open=True,
        open_date=ts("2025-01-08 00:00:05"),
        close_date=None,
        open_rate=120.0,
        close_rate=None,
        amount=8 / 120,
        fee_close=None,
        profit_abs=None,
        profit_ratio=None,
        exit_reason=None,
    )
    report = forward.performance_report([closed, open_trade], candles, 8.0, STEP)
    # Closed trade: realized. Open trade: marked at the last close (120), paying the exit fee.
    realized = closed.profit_abs
    unrealized = (8 / 120) * 120 * 0.996 - (8 / 120) * 120 * 1.004
    assert report["final_equity"] == pytest.approx(8.0 + realized + unrealized, abs=1e-4)
    assert report["net_return_pct"] == pytest.approx((realized + unrealized) / 8 * 100, abs=1e-2)
    assert report["open_trades"] == 1
    assert report["closed_trades"]["count"] == 1
    assert report["mtm_max_drawdown_pct"] >= 0
    assert report["equity_daily"][0]["date"] == "2025-01-01"
    assert 0 < report["exposure_pct"] < 100


def test_performance_report_refuses_trades_before_the_candles() -> None:
    candles = make_candles([100.0] * 12, start="2025-02-01")
    with pytest.raises(ValueError, match="longer history"):
        forward.performance_report([paper_trade()], candles, 8.0, STEP)


def test_performance_report_without_trades_is_flat() -> None:
    candles = make_candles([100.0, 101.0, 99.0], start="2025-02-01")
    report = forward.performance_report([], candles, 8.0, STEP)
    assert report["net_return_pct"] == 0 and report["mtm_max_drawdown_pct"] == 0
    assert report["exposure_pct"] == 0.0 and report["closed_trades"] == {"count": 0}


# --- criteria -----------------------------------------------------------------


def criteria_inputs(**overrides):
    performance = {"closed_trades": {"count": 3}, "mtm_max_drawdown_pct": 10.0}
    execution = {
        "summary": {
            "closed_round_trips_measured": 3,
            "assumed_round_trip_cost_pct": 1.0,
            "stress_round_trip_cost_pct": 2.0,
            "round_trip_cost_pct_mean": 0.9,
        }
    }
    fidelity = {
        "entry_signals_in_window": 3,
        "entries_matched": 3,
        "entries_without_signal": [],
        "signals_without_entry_unexplained": [],
    }
    for key, value in overrides.items():
        target = {"performance": performance, "execution": execution, "fidelity": fidelity}[key[0]]
        target.update(value)
    return performance, execution, fidelity


def by_id(result: dict) -> dict:
    return {c["id"]: c for c in result["criteria"]}


def test_criteria_continue_and_tracking_only() -> None:
    result = forward.evaluate_criteria(
        *criteria_inputs(), max_drawdown_pct=31.31, trades_per_year_expected=6.3
    )
    ids = by_id(result)
    assert result["verdict"] == "CONTINUE" and not result["performance_judgement_allowed"]
    assert ids["F1"]["result"] == "PASS" and ids["F2"]["result"] == "PASS"
    assert ids["F2"]["within_base_assumption"] is True
    assert ids["F3"]["result"] == "PASS"
    assert ids["F4"]["result"] == "NOT_YET_TESTABLE" and ids["F4"]["trades_remaining"] == 27
    assert ids["F4"]["years_remaining_at_expected_rate"] == pytest.approx(27 / 6.3, abs=0.05)


def test_criteria_stop_on_drawdown_fidelity_or_cost() -> None:
    perf, exe, fid = criteria_inputs()
    perf["mtm_max_drawdown_pct"] = 40.0
    assert forward.evaluate_criteria(perf, exe, fid, max_drawdown_pct=31.31)["verdict"] == "STOP"

    perf, exe, fid = criteria_inputs()
    fid["entries_without_signal"] = [{"trade_id": 9}]
    result = forward.evaluate_criteria(perf, exe, fid, max_drawdown_pct=31.31)
    assert result["verdict"] == "STOP" and by_id(result)["F1"]["result"] == "FAIL"

    perf, exe, fid = criteria_inputs()
    exe["summary"]["round_trip_cost_pct_mean"] = 2.5
    result = forward.evaluate_criteria(perf, exe, fid, max_drawdown_pct=31.31)
    assert result["verdict"] == "STOP" and by_id(result)["F2"]["result"] == "FAIL"


def test_criteria_not_yet_testable_without_data() -> None:
    perf, exe, fid = criteria_inputs()
    perf["closed_trades"] = {"count": 0}
    exe["summary"]["closed_round_trips_measured"] = 0
    del exe["summary"]["round_trip_cost_pct_mean"]
    fid.update({"entry_signals_in_window": 0, "entries_matched": 0})
    result = forward.evaluate_criteria(perf, exe, fid, max_drawdown_pct=31.31)
    ids = by_id(result)
    assert ids["F1"]["result"] == ids["F2"]["result"] == "NOT_YET_TESTABLE"
    assert ids["F2"]["within_base_assumption"] is None
    assert result["verdict"] == "CONTINUE"


def test_criteria_judgement_allowed_at_min_trades() -> None:
    perf, exe, fid = criteria_inputs()
    perf["closed_trades"] = {"count": forward.MIN_TRADES_FOR_JUDGEMENT}
    assert forward.evaluate_criteria(perf, exe, fid, max_drawdown_pct=31.31)[
        "performance_judgement_allowed"
    ]


# --- assembly -------------------------------------------------------------------


def test_build_report_end_to_end_with_h1_signals() -> None:
    closes = [100.0] * 130 + [120.0] + [120.0] * 29  # breakout at candle 130 (2025-01-22 16:00)
    candles = make_candles(closes, start="2025-01-01")
    signal = candles["date"].iloc[130]
    trade = paper_trade(
        id=1,
        open_date=signal + STEP + pd.Timedelta(seconds=3),
        close_date=None,
        is_open=True,
        open_rate=120.0,
        close_rate=None,
        amount=8 / 120,
        fee_close=None,
        profit_abs=None,
        profit_ratio=None,
        exit_reason=None,
    )
    frame = forward.compute_signals(H1ChannelBreakout({}), candles, "BTC/EUR")
    report = forward.build_report(
        trades=[trade],
        candles=candles,
        entry_signals=forward.signal_times(frame, "enter_long"),
        order_counts={"entry:closed": 1},
        notional=8.0,
        timeframe="4h",
        pair="BTC/EUR",
        source={"db": "x", "mode": "dry_run", "strategy": "H1ChannelBreakout", "candles": "test"},
        since=None,
        max_drawdown_pct=31.31,
        trades_per_year_expected=6.3,
        signals_valid_from=candles["date"].iloc[H1ChannelBreakout.startup_candle_count],
    )
    assert report["fidelity"]["entries_matched"] == 1
    assert report["fidelity"]["entries_without_signal"] == []
    assert report["criteria"]["verdict"] == "CONTINUE"
    assert report["performance"]["open_trades"] == 1
    # Without --since the window starts at the first trade's candle (131 of 160).
    assert report["window"]["candles"] == 29
    assert report["fidelity"]["window_start_source"] == "first trade"
    json.dumps(report)  # serialisable


def test_build_report_refuses_a_position_straddling_the_window_start() -> None:
    candles = make_candles([100.0] * 60, start="2025-01-01")
    trade = paper_trade(open_date=ts("2025-01-02"), close_date=ts("2025-01-06"))
    with pytest.raises(ValueError, match="open at the window start"):
        forward.build_report(
            trades=[trade],
            candles=candles,
            entry_signals=[],
            order_counts={},
            notional=8.0,
            timeframe="4h",
            pair="BTC/EUR",
            source={},
            since=ts("2025-01-04"),
            max_drawdown_pct=31.31,
            trades_per_year_expected=None,
        )


def test_parse_utc_arg() -> None:
    assert forward.parse_utc_arg("2025-01-01") == ts("2025-01-01")
    assert forward.parse_utc_arg("2025-01-01T02:00:00+02:00") == ts("2025-01-01")


def test_record_k2_limit_and_trades_per_year_from_the_tracked_record() -> None:
    root = Path(__file__).resolve().parents[1]
    assert forward.record_k2_limit(root) == pytest.approx(31.3095, abs=1e-3)
    assert forward.record_trades_per_year(root) == pytest.approx(6.305, abs=1e-2)
    assert forward.record_k2_limit(Path("/nonexistent")) is None


# --- hardening (security review 2026-10-08) -------------------------------------


def test_read_only_uri_encodes_special_characters(tmp_path: Path) -> None:
    assert forward.read_only_uri("/a/b.sqlite") == "file:/a/b.sqlite?mode=ro"
    # A "?" in the name must not swallow mode=ro: the connection stays read-only
    # and does not create a file.
    odd = tmp_path / "a?b.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect(forward.read_only_uri(str(odd)), uri=True).execute("CREATE TABLE t (x)")
    assert not odd.exists() and not (tmp_path / "a").exists()


def test_validate_max_drawdown() -> None:
    assert forward.validate_max_drawdown(31.31) == 31.31
    for bad in (0, -5, 100, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            forward.validate_max_drawdown(bad)


def test_validate_out_path(tmp_path: Path) -> None:
    ok = tmp_path / "reports" / "forward-2026-10-08.json"
    assert forward.validate_out_path(ok) == ok.resolve()
    with pytest.raises(ValueError):
        forward.validate_out_path(tmp_path / "reports" / "dry-run.sqlite")
    with pytest.raises(ValueError):
        forward.validate_out_path(tmp_path / "runtime" / "forward.json")


def test_build_report_refuses_trades_dropped_by_a_short_history() -> None:
    candles = make_candles([100.0] * 60, start="2025-02-01")
    old_trade = paper_trade(open_date=ts("2025-01-02"), close_date=ts("2025-01-06"))
    kwargs = dict(
        trades=[old_trade],
        candles=candles,
        entry_signals=[],
        order_counts={},
        notional=8.0,
        timeframe="4h",
        pair="BTC/EUR",
        source={},
        max_drawdown_pct=31.31,
        trades_per_year_expected=None,
    )
    with pytest.raises(ValueError, match="predate the candle history"):
        forward.build_report(since=None, **kwargs)
    # An explicit --since after the old trade starts a new window deliberately.
    report = forward.build_report(since=ts("2025-02-01"), **kwargs)
    assert report["performance"]["closed_trades"] == {"count": 0}


# --- review fixes (2026-10-08) ------------------------------------------------------


def test_trades_frame_aligns_fills_to_candles() -> None:
    """A fill a few seconds into a candle counts for that whole candle, as in
    the backtest; the exit candle is no longer held (reviewer case A/B)."""
    candles = make_candles([100.0, 100.0, 70.0, 100.0, 80.0], start="2025-01-01")
    # Entry 2025-01-01 04:00:05 at 100; the entry candle closes at 70 (-30 %).
    # Exit 2025-01-01 12:00:05 at 100; the exit candle closes at 80.
    trade = paper_trade(
        open_date=ts("2025-01-01 04:00:05"),
        close_date=ts("2025-01-01 12:00:05"),
        open_rate=100.0,
        close_rate=100.0,
        amount=0.08,
        fee_open=0.0,
        fee_close=0.0,
        profit_abs=0.0,
        profit_ratio=0.0,
    )
    report = forward.performance_report([trade], candles, 8.0, STEP)
    # Flat after the exit, so the final equity is the realised balance, not the exit candle's close.
    assert report["final_equity"] == pytest.approx(8.0)
    # The entry candle's -30 % close is visible in the drawdown.
    assert report["mtm_max_drawdown_pct"] == pytest.approx(30.0, abs=1e-6)


def test_execution_ignores_the_next_open_reference_for_stop_exits() -> None:
    candles = make_candles([100.0] * 60, start="2025-01-01")
    stop = paper_trade(exit_reason="stop_loss", close_rate=80.0)
    execution = forward.execution_of(stop, candles, STEP)
    assert execution.exit_slippage_bps is None and execution.round_trip_cost_pct is None
    assert execution.exit_reason == "stop_loss"
    signal_exit = forward.execution_of(paper_trade(exit_reason="exit_signal"), candles, STEP)
    assert signal_exit.round_trip_cost_pct is not None


def test_signal_fidelity_counts_the_candle_before_the_window_and_acknowledgements() -> None:
    ws, end = ts("2025-01-10"), ts("2025-02-01")
    pre = ws - STEP  # the candle that closed exactly at the window start
    trade = paper_trade(id=1, open_date=ws + pd.Timedelta(seconds=4), close_date=ts("2025-01-12"))
    drill = ts("2025-01-20 00:00")
    result = forward.signal_fidelity(
        [pre, drill], [trade], STEP, window_start=ws, window_end=end, acknowledged=[drill]
    )
    assert result["entries_matched"] == 1 and result["entries_without_signal"] == []
    reasons = {e["signal_candle"]: e["reason"] for e in result["signals_without_entry_explained"]}
    assert reasons[drill.isoformat()] == "acknowledged by the operator"
    assert result["signals_without_entry_unexplained"] == []


def test_build_report_without_trades_or_since_has_no_fidelity_window() -> None:
    """Signals from before the bot existed must not fail F1 on day one."""
    closes = [100.0] * 130 + [120.0] + [120.0] * 29
    candles = make_candles(closes, start="2025-01-01")
    frame = forward.compute_signals(H1ChannelBreakout({}), candles, "BTC/EUR")
    report = forward.build_report(
        trades=[],
        candles=candles,
        entry_signals=forward.signal_times(frame, "enter_long"),
        order_counts={},
        notional=8.0,
        timeframe="4h",
        pair="BTC/EUR",
        source={},
        since=None,
        max_drawdown_pct=31.31,
        trades_per_year_expected=None,
        signals_valid_from=candles["date"].iloc[H1ChannelBreakout.startup_candle_count],
    )
    assert report["fidelity"]["window_start_source"] == "none (no trades yet)"
    assert report["fidelity"]["signals_without_entry_unexplained"] == []
    assert {c["id"]: c["result"] for c in report["criteria"]["criteria"]}[
        "F1"
    ] == "NOT_YET_TESTABLE"
    assert report["criteria"]["verdict"] == "CONTINUE"


def test_closed_candles_drops_the_forming_one() -> None:
    candles = make_candles([1.0, 2.0, 3.0], start="2025-01-01")  # 00:00, 04:00, 08:00
    now = ts("2025-01-01 11:59")
    assert len(forward.closed_candles(candles, STEP, now)) == 2
    assert len(forward.closed_candles(candles, STEP, ts("2025-01-01 12:00"))) == 3
