import json
import sqlite3

import numpy as np
import pytest
from conftest import make_candles
from test_forward import STEP, create_db, paper_trade, ts

from sq.live import forward
from sq.research import h2, statistics


def report_for(candles, trades, **kwargs):
    defaults = dict(
        trades=trades,
        candles=candles,
        entry_signals=[],
        order_counts={},
        notional=8.0,
        timeframe="4h",
        pair="BTC/EUR",
        source={},
        since=None,
        max_drawdown_pct=31.31,
        trades_per_year_expected=6.3,
    )
    defaults.update(kwargs)
    return forward.build_report(**defaults)


def test_first_candle_loss_triggers_stop():
    candles = make_candles([60.0] * 6)
    t = paper_trade(
        open_date=candles["date"].iloc[0],
        is_open=True,
        close_date=None,
        profit_abs=None,
        profit_ratio=None,
        fee_open=0.0,
        open_rate=100.0,
    )
    result = report_for(candles, [t])
    assert result["performance"]["mtm_max_drawdown_pct"] == 40.0
    assert result["criteria"]["verdict"] == "STOP"


def test_full_history_used_for_h2_and_reference_capital():
    rng = np.random.RandomState(0)
    candles = make_candles(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))))
    t = paper_trade(open_date=candles["date"].iloc[200], close_date=candles["date"].iloc[220])
    result = report_for(candles, [t])
    expected = h2.stake_fraction(h2.realized_volatility(candles["close"]).iloc[199])
    assert result["h2_paired"]["stake_fractions"][0] == pytest.approx(expected, abs=0.0001)
    assert result["h2_paired"]["notional"] == 1000.0
    assert result["h2_paired"]["missing_sizing_history"] == 0
    assert result["h2_paired"]["verdict"] == "INCONCLUSIVE"


def test_h2_missing_warmup_cannot_approve_even_with_30_trades():
    candles = make_candles([100.0] * 5)
    frame = forward.mark_frame(
        [paper_trade(open_date=candles["date"].iloc[0], close_date=candles["date"].iloc[2])],
        candles,
        STEP,
    )
    result = h2.paired_evaluation(frame, candles, 1000.0, STEP, closed_trades=30)
    assert result["missing_sizing_history"] == 1
    assert not result["judgement_allowed"]


def test_account_capital_is_separate_from_fixed_stake():
    candles = make_candles([100.0] * 60)
    result = report_for(candles, [paper_trade()], initial_capital=10.0)
    assert result["performance"]["net_return_pct"] == pytest.approx(0.7355 / 8 * 100, abs=0.0001)
    assert result["account"]["net_return_pct"] == 7.355


def test_asof_excludes_future_entries_and_future_realized_pnl():
    candles = make_candles([100.0] * 30)
    t = paper_trade(close_date=ts("2025-01-10"), close_rate=200.0, profit_abs=8.0, profit_ratio=1.0)
    future = paper_trade(id=2, open_date=ts("2025-01-11"), close_date=ts("2025-01-12"))
    r = report_for(candles, [t, future])
    assert r["performance"]["closed_trades"]["count"] == 0
    assert r["performance"]["open_trades"] == 1
    assert r["performance"]["net_return_pct"] < 0
    assert r["data_quality"]["entries_after_mark_excluded"] == 1


def test_missing_start_rejected_even_without_trades():
    with pytest.raises(forward.ReportInputError, match="predates"):
        report_for(make_candles([100.0] * 10), [], since=ts("2024-12-01"))


@pytest.mark.parametrize(
    "change",
    [
        dict(strategy="Different"),
        dict(timeframe="1h"),
        dict(amount=0),
        dict(fee_open=float("nan")),
        dict(profit_abs=float("inf")),
    ],
)
def test_incompatible_trades_fail(change):
    with pytest.raises(forward.ReportInputError):
        report_for(
            make_candles([100.0] * 60),
            [paper_trade(**change)],
            source={"strategy": "H1ChannelBreakout"},
        )


def test_performance_gates_and_bootstrap_are_evaluated():
    trades = [paper_trade(id=i, profit_ratio=-0.01) for i in range(30)]
    r = forward.performance_decision(
        {"net_return_pct": -10, "mtm_max_drawdown_pct": 10},
        {"mtm_max_drawdown_pct": 10},
        {"performance_judgement_allowed": True},
        trades,
    )
    assert [c["result"] for c in r["criteria"]] == ["FAIL", "FAIL"]
    assert not r["positive_mean_supported"] and not r["capital_increase_allowed"]
    assert r["trade_uncertainty"]["mean_ci95_pct"] == [-1.0, -1.0]


def test_missing_signal_coverage_withholds_judgement():
    performance = {"closed_trades": {"count": 30}, "mtm_max_drawdown_pct": 0}
    fidelity = {
        "entries_without_signal": [],
        "signals_without_entry_unexplained": [],
        "entry_signals_in_window": 4,
        "entries_matched": 4,
        "entries_unverifiable": [{}],
    }
    execution = {"summary": {"closed_round_trips_measured": 0, "stress_round_trip_cost_pct": 2}}
    result = forward.evaluate_criteria(performance, execution, fidelity, max_drawdown_pct=31.0)
    assert result["criteria"][0]["result"] == "NOT_YET_TESTABLE"
    assert not result["performance_judgement_allowed"]


@pytest.mark.parametrize("unexpected", [False, True])
def test_missing_startup_coverage_cannot_pass_after_later_trades(unexpected):
    candles = make_candles([100.0] * 300)
    trades = [
        paper_trade(
            id=i,
            open_date=candles["date"].iloc[150 + 3 * i],
            close_date=candles["date"].iloc[151 + 3 * i],
        )
        for i in range(30)
    ]
    signals = [t.open_date - STEP for t in trades]
    if unexpected:
        signals.pop()
    result = report_for(
        candles,
        trades,
        since=candles["date"].iloc[0],
        signals_valid_from=candles["date"].iloc[122],
        entry_signals=signals,
    )
    assert result["fidelity"]["entries_unverifiable"] == []
    assert not result["fidelity"]["window_coverage_complete"]
    criteria = {c["id"]: c["result"] for c in result["criteria"]["criteria"]}
    assert criteria["F1"] == ("FAIL" if unexpected else "NOT_YET_TESTABLE")
    assert criteria["P1"] == criteria["P2"] == "NOT_YET_TESTABLE"
    assert not result["criteria"]["performance_judgement_allowed"]
    assert not result["h2_paired"]["evidence_valid"]


def test_delayed_fill_uses_submission_time_for_signal_coverage():
    start = ts("2025-01-05")
    trade = paper_trade(open_date=start + STEP, submitted_date=start)
    fidelity = forward.signal_fidelity(
        [],
        [trade],
        STEP,
        window_start=start,
        window_end=start + 3 * STEP,
        signals_valid_from=start,
    )
    assert len(fidelity["entries_unverifiable"]) == 1
    assert fidelity["entries_without_signal"] == []


def test_signal_coverage_includes_candle_preceding_start():
    start = ts("2025-01-05")
    fidelity = forward.signal_fidelity(
        [start - STEP],
        [paper_trade()],
        STEP,
        window_start=start,
        window_end=start + 2 * STEP,
        signals_valid_from=start - STEP,
    )
    assert fidelity["window_coverage_complete"]
    assert fidelity["entries_matched"] == 1


def test_flat_bootstrap_and_fee_drawdown():
    r = statistics.block_bootstrap_sharpe(np.zeros(60), resamples=10)
    assert r["sharpe_ci95"] is None
    b = statistics.constant_exposure_benchmark([100.0, 100.0, 100.0], 1.0, fee=0.01, notional=100.0)
    assert b["max_drawdown_pct"] == 1.99


def test_fill_delay_uses_fill_clock_but_submission_reference():
    candles = make_candles([100.0] * 60)
    t = paper_trade(open_date=ts("2025-01-05 00:09"), submitted_date=ts("2025-01-05 00:00:05"))
    assert forward.execution_of(t, candles, STEP).entry_delay_minutes == 9.0


def test_signal_does_not_match_one_candle_late():
    signal = ts("2025-01-01")
    trade = paper_trade(open_date=signal + 2 * STEP)
    fidelity = forward.signal_fidelity(
        [signal], [trade], STEP, window_start=signal, window_end=signal + 4 * STEP
    )
    assert len(fidelity["entries_without_signal"]) == 1


def fill_db(tmp_path, *, filled=0.08, partial=False, missing_time=False, multiple=False):
    t = paper_trade(
        is_open=True, close_date=None, close_rate=None, profit_abs=None, profit_ratio=None
    )
    row = dict(
        id=t.id,
        pair=t.pair,
        is_open=1,
        open_date="2025-01-05 00:00:05",
        close_date=None,
        open_rate=100,
        close_rate=None,
        amount=0.08,
        stake_amount=8,
        fee_open=0.004,
        fee_close=None,
        close_profit_abs=None,
        close_profit=None,
        exit_reason=None,
        strategy=t.strategy,
        timeframe="240",
    )
    path = create_db(tmp_path / "test.sqlite", [row], [])
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE trades ADD COLUMN amount_precision REAL DEFAULT 0.00000001")
        db.execute("ALTER TABLE trades ADD COLUMN precision_mode INTEGER DEFAULT 4")
        db.execute("DROP TABLE orders")
        db.execute(
            "CREATE TABLE orders (id INTEGER, ft_trade_id INTEGER, ft_pair TEXT, "
            "ft_order_side TEXT, status TEXT, ft_is_open INTEGER, filled REAL, average REAL, "
            "price REAL, order_date TEXT, order_filled_date TEXT, ft_fee_base REAL)"
        )
        order = (
            1,
            1,
            "BTC/EUR",
            "buy",
            "open" if partial else "closed",
            partial,
            filled,
            100.0,
            100.0,
            "2025-01-05 00:00:05",
            None if missing_time else "2025-01-05 00:09:00",
            0.0,
        )
        db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", order)
        if multiple:
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (2, *order[1:]))
    return path


def test_snapshot_reads_fill_time_and_excludes_unfilled_intent(tmp_path):
    path = fill_db(tmp_path)
    trades, counts = forward.filled_snapshot(path, ["BTC/EUR"])
    assert trades[0].open_date == ts("2025-01-05 00:09")
    assert trades[0].submitted_date == ts("2025-01-05 00:00:05")
    assert counts == {"buy:closed": 1}
    with sqlite3.connect(path) as db:
        db.execute('UPDATE orders SET filled=0, ft_is_open=1, status="open"')
    assert forward.filled_snapshot(path, ["BTC/EUR"])[0] == []


@pytest.mark.parametrize(
    "options", [dict(partial=True), dict(missing_time=True), dict(multiple=True), dict(filled=0.04)]
)
def test_unsupported_fill_states_fail(tmp_path, options):
    with pytest.raises(forward.ReportInputError):
        forward.filled_snapshot(fill_db(tmp_path, **options), ["BTC/EUR"])


def test_atomic_report_roundtrips_strict_json(tmp_path):
    path = tmp_path / "report.json"
    forward.atomic_report(path, {"value": 1})
    with pytest.raises(ValueError):
        forward.atomic_report(path, {"value": float("nan")})
    assert json.loads(path.read_text()) == {"value": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_small_base_currency_fee_respects_exchange_precision(tmp_path):
    path = fill_db(tmp_path, filled=0.00008)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE trades SET amount=.00007979")
        db.execute("UPDATE orders SET ft_fee_base=.000000208")
    trades, _ = forward.filled_snapshot(path, ["BTC/EUR"])
    assert trades[0].amount == 0.00007979


def test_h2_invalid_evidence_and_identical_zero_drawdown_cannot_judge():
    candles = make_candles([100.0] * 400)
    trades = [
        paper_trade(
            id=i,
            open_date=candles["date"].iloc[130 + 3 * i],
            close_date=candles["date"].iloc[131 + 3 * i],
            fee_open=0,
            fee_close=0,
            profit_abs=0.1,
            profit_ratio=0.01,
        )
        for i in range(30)
    ]
    frame = forward.mark_frame(trades, candles, STEP)
    # Zero volatility uses floor but both smooth curves have no drawdown.
    result = h2.paired_evaluation(frame, candles, 8.0, STEP, closed_trades=30, evidence_valid=True)
    assert result["h1"]["mtm_max_drawdown_pct"] == 0
    assert result["h2"]["mtm_max_drawdown_pct"] == 0
    assert result["verdict"] == "INCONCLUSIVE"
    result = h2.paired_evaluation(
        frame, candles, 8.0, STEP, closed_trades=30, evidence_valid=False, stopped=True
    )
    assert not result["judgement_allowed"]
