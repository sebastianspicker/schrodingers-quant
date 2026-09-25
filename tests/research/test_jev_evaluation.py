"""Tests for sq.research.jev_evaluation: matching, leakage exclusion, and
summaries.

Uses plain lists of trade/assessment dicts; no Freqtrade backtest export and
no network.
"""

from datetime import UTC, datetime, timedelta

import pytest

from sq.research import jev_evaluation as evaluate

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_trade(open_offset_hours: float, profit_abs: float, pair: str = "BTC/EUR") -> dict:
    open_date = BASE_TIME + timedelta(hours=open_offset_hours)
    return {
        "pair": pair,
        "open_date": open_date,
        "close_date": open_date + timedelta(hours=4),
        "open_rate": 100.0,
        "amount": 1.0,
        "fee_open": 0.0,
        "fee_close": 0.0,
        "profit_abs": profit_abs,
    }


def make_assessment(
    candidate_signal_offset_hours: float,
    available_offset_hours: float,
    decision: str,
    pair: str = "BTC/EUR",
    candidate_id: str | None = None,
) -> dict:
    signal_time = BASE_TIME + timedelta(hours=candidate_signal_offset_hours)
    available_at = BASE_TIME + timedelta(hours=available_offset_hours)
    cid = candidate_id or f"cand-{candidate_signal_offset_hours}-{pair}"
    return {
        "candidate_id": cid,
        "input": {
            "candidate_id": cid,
            "pair": pair,
            "signal_candle_time": signal_time.isoformat(),
            "rate": 100.0,
        },
        "input_timestamp": signal_time.isoformat(),
        "model": "test",
        "model_version": "v1",
        "prompt_version": "sha256:test",
        "decision": decision,
        "rationale": "test",
        "confidence": None,
        "requested_at": signal_time.isoformat(),
        "available_at": available_at.isoformat(),
        "latency_ms": 1.0,
        "error": None,
    }


def test_approved_before_entry_is_included():
    trades = evaluate.load_trades([make_trade(open_offset_hours=0.1, profit_abs=5.0)])
    assessments = [make_assessment(0.0, available_offset_hours=0.05, decision="approve")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    assert report["baseline"]["trade_count"] == 1
    assert report["filtered"]["trade_count"] == 1
    assert report["skipped_trades"] == 0
    assert report["trades_lost_for_lack_of_timely_assessment"] == 0


def test_late_assessment_is_excluded_no_leakage():
    trades = evaluate.load_trades([make_trade(open_offset_hours=0.1, profit_abs=5.0)])
    # Assessment becomes available AFTER the trade already entered.
    assessments = [make_assessment(0.0, available_offset_hours=0.2, decision="approve")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    assert report["baseline"]["trade_count"] == 1
    assert report["filtered"]["trade_count"] == 0
    assert report["skipped_trades"] == 1
    assert report["trades_lost_for_lack_of_timely_assessment"] == 1


def test_rejected_decision_is_excluded_but_not_counted_as_lost():
    trades = evaluate.load_trades([make_trade(open_offset_hours=0.1, profit_abs=5.0)])
    assessments = [make_assessment(0.0, available_offset_hours=0.05, decision="reject")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    assert report["filtered"]["trade_count"] == 0
    assert report["skipped_trades"] == 1
    assert report["trades_lost_for_lack_of_timely_assessment"] == 0
    assert report["rejected_trades"] == 1


def test_abstain_decision_is_excluded():
    trades = evaluate.load_trades([make_trade(open_offset_hours=0.1, profit_abs=5.0)])
    assessments = [make_assessment(0.0, available_offset_hours=0.05, decision="abstain")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    assert report["filtered"]["trade_count"] == 0


def test_no_matching_candidate_counts_as_lost_for_lack_of_assessment():
    trades = evaluate.load_trades([make_trade(open_offset_hours=0.1, profit_abs=5.0)])

    report = evaluate.evaluate(trades, assessments=[], wallet=100.0)

    assert report["baseline"]["trade_count"] == 1
    assert report["filtered"]["trade_count"] == 0
    assert report["trades_lost_for_lack_of_timely_assessment"] == 1


def test_candidate_beyond_max_lag_hours_is_treated_as_no_assessment():
    trades = evaluate.load_trades([make_trade(open_offset_hours=20.0, profit_abs=5.0)])
    # Candidate signal was 20h before the trade; default max_lag_hours=8.
    assessments = [make_assessment(0.0, available_offset_hours=0.05, decision="approve")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0, max_lag_hours=8.0)

    assert report["filtered"]["trade_count"] == 0
    assert report["trades_lost_for_lack_of_timely_assessment"] == 1


def test_net_return_uses_the_fixed_notional():
    trades = evaluate.load_trades(
        [
            make_trade(open_offset_hours=0.0, profit_abs=10.0),
            make_trade(open_offset_hours=5.0, profit_abs=-2.0),
        ]
    )
    assessments = [
        make_assessment(-0.1, available_offset_hours=-0.05, decision="approve"),
        make_assessment(4.9, available_offset_hours=4.95, decision="approve"),
    ]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    assert report["baseline"]["net_return_pct"] == pytest.approx(8.0)
    assert report["baseline"]["final_equity"] == pytest.approx(108.0)
    assert report["filtered"]["trade_count"] == 2


def test_each_candidate_matches_at_most_one_trade():
    # Two trades, only one candidate available before both: only the nearer
    # trade may claim it; the earlier trade has no candidate before it and no
    # candidate should be reused for it after the fact.
    trades = evaluate.load_trades(
        [
            make_trade(open_offset_hours=0.0, profit_abs=1.0),
            make_trade(open_offset_hours=1.0, profit_abs=1.0),
        ]
    )
    assessments = [make_assessment(0.5, available_offset_hours=0.6, decision="approve")]

    report = evaluate.evaluate(trades, assessments, wallet=100.0)

    # Only the second trade (open_date 1h) has a candidate at or before it
    # (signal at 0.5h); the first trade (open_date 0h) has none.
    assert report["filtered"]["trade_count"] == 1
    assert report["trades_lost_for_lack_of_timely_assessment"] == 1
