"""Tests for H1JevShadow: candidate identity, dedupe, and off/shadow/filter modes.

Synthetic-dataframe unit tests only; no Freqtrade backtester, no real Jev
provider, no exchange, no network.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from H1ChannelBreakout import H1ChannelBreakout
from H1JevShadow import H1JevShadow, _candidate_id, _strategy_identity

SIGNAL_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeDataProvider:
    """Minimal stand-in for Freqtrade's DataProvider.get_analyzed_dataframe."""

    def __init__(self, dataframe: pd.DataFrame):
        self._dataframe = dataframe

    def get_analyzed_dataframe(self, pair: str, timeframe: str):
        return self._dataframe, "cached"


class RaisingDataProvider:
    def get_analyzed_dataframe(self, pair: str, timeframe: str):
        raise RuntimeError("boom: simulated DataProvider failure")


def make_dataframe(last_candle_time: datetime = SIGNAL_TIME) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [last_candle_time - timedelta(hours=4), last_candle_time],
            "close": [100.0, 101.0],
        }
    )


def make_strategy(tmp_path: Path, mode: str | None, ttl_minutes: float = 60.0) -> H1JevShadow:
    config: dict = {"stake_currency": "EUR"}
    if mode is not None:
        config["jev"] = {
            "mode": mode,
            "assessment_ttl_minutes": ttl_minutes,
            "dir": str(tmp_path),
        }
    strategy = H1JevShadow(config)
    strategy.dp = FakeDataProvider(make_dataframe())
    return strategy


def confirm_entry(strategy: H1JevShadow, pair: str = "BTC/EUR", rate: float = 100.0) -> bool:
    return strategy.confirm_trade_entry(
        pair=pair,
        order_type="limit",
        amount=1.0,
        rate=rate,
        time_in_force="GTC",
        current_time=datetime.now(UTC),
        entry_tag=None,
        side="long",
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_assessment(path: Path, candidate_id: str, **overrides) -> None:
    record = {
        "candidate_id": candidate_id,
        "input": {},
        "input_timestamp": datetime.now(UTC).isoformat(),
        "model": "null",
        "model_version": "v0",
        "prompt_version": "sha256:test",
        "decision": "approve",
        "rationale": "test",
        "confidence": None,
        "requested_at": datetime.now(UTC).isoformat(),
        "available_at": datetime.now(UTC).isoformat(),
        "latency_ms": 1.0,
        "error": None,
    }
    record.update(overrides)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# --- inheritance: exits, stoploss and protections are untouched ----------


def test_signal_and_exit_methods_are_inherited_unchanged():
    assert H1JevShadow.populate_indicators is H1ChannelBreakout.populate_indicators
    assert H1JevShadow.populate_entry_trend is H1ChannelBreakout.populate_entry_trend
    assert H1JevShadow.populate_exit_trend is H1ChannelBreakout.populate_exit_trend
    assert H1JevShadow.protections.fget is H1ChannelBreakout.protections.fget


def test_risk_parameters_are_inherited_unchanged():
    assert H1JevShadow.stoploss == H1ChannelBreakout.stoploss
    assert H1JevShadow.trailing_stop == H1ChannelBreakout.trailing_stop
    assert H1JevShadow.use_exit_signal == H1ChannelBreakout.use_exit_signal
    assert H1JevShadow.exit_profit_only == H1ChannelBreakout.exit_profit_only
    assert H1JevShadow.minimal_roi == H1ChannelBreakout.minimal_roi
    assert H1JevShadow.order_types == H1ChannelBreakout.order_types
    assert H1JevShadow.timeframe == H1ChannelBreakout.timeframe
    assert issubclass(H1JevShadow, H1ChannelBreakout)


def test_confirm_trade_entry_is_the_only_override():
    # H1JevShadow's own __dict__ (not inherited) defines confirm_trade_entry
    # and its private _jev_* helpers, and nothing that Freqtrade calls for
    # signals, exits or protections.
    own_public_overrides = {
        name
        for name in vars(H1JevShadow)
        if not name.startswith("_jev") and not name.startswith("_abc") and not name.startswith("__")
    }
    assert own_public_overrides == {"confirm_trade_entry"}


# --- candidate identity and dedupe ----------------------------------------


def test_candidate_id_is_stable_for_the_same_inputs():
    version = _strategy_identity(H1JevShadow)
    id_a = _candidate_id("H1JevShadow", "BTC/EUR", SIGNAL_TIME, version)
    id_b = _candidate_id("H1JevShadow", "BTC/EUR", SIGNAL_TIME, version)
    assert id_a == id_b


def test_candidate_id_changes_with_pair_or_signal_time():
    version = _strategy_identity(H1JevShadow)
    base = _candidate_id("H1JevShadow", "BTC/EUR", SIGNAL_TIME, version)
    other_pair = _candidate_id("H1JevShadow", "ETH/EUR", SIGNAL_TIME, version)
    other_time = _candidate_id("H1JevShadow", "BTC/EUR", SIGNAL_TIME + timedelta(hours=4), version)
    assert base != other_pair
    assert base != other_time


def test_shadow_mode_records_one_candidate_and_dedupes_across_calls(tmp_path):
    strategy = make_strategy(tmp_path, mode="shadow")

    first = confirm_entry(strategy)
    second = confirm_entry(strategy)  # simulates a confirm_trade_entry retry

    assert first is True
    assert second is True
    records = read_jsonl(tmp_path / "candidates.jsonl")
    assert len(records) == 1
    assert records[0]["pair"] == "BTC/EUR"
    assert records[0]["rate"] == 100.0
    assert records[0]["strategy_name"] == "H1JevShadow"


def test_shadow_mode_dedupes_across_strategy_instances(tmp_path):
    # A restart creates a fresh instance; dedupe must survive that, since it
    # is based on the file, not only an in-memory set.
    confirm_entry(make_strategy(tmp_path, mode="shadow"))
    confirm_entry(make_strategy(tmp_path, mode="shadow"))

    records = read_jsonl(tmp_path / "candidates.jsonl")
    assert len(records) == 1


def test_shadow_mode_never_blocks_orders_even_without_a_data_provider(tmp_path):
    strategy = make_strategy(tmp_path, mode="shadow")
    strategy.dp = None  # simulates a misconfigured or backtesting instance

    assert confirm_entry(strategy) is True
    assert read_jsonl(tmp_path / "candidates.jsonl") == []


# --- off mode --------------------------------------------------------


def test_off_mode_is_the_default_and_does_no_io(tmp_path):
    strategy = H1JevShadow({"stake_currency": "EUR"})
    strategy.dp = RaisingDataProvider()  # off mode must never even touch dp

    assert confirm_entry(strategy) is True
    assert not (tmp_path / "candidates.jsonl").exists()


def test_off_mode_explicit(tmp_path):
    strategy = make_strategy(tmp_path, mode="off")
    assert confirm_entry(strategy) is True
    assert not (tmp_path / "candidates.jsonl").exists()


# --- filter mode -----------------------------------------------------


def test_filter_mode_blocks_when_no_assessment_exists(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    assert confirm_entry(strategy) is False
    # The candidate is still recorded even though the entry is blocked.
    assert len(read_jsonl(tmp_path / "candidates.jsonl")) == 1


def test_filter_mode_allows_a_fresh_approval(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    confirm_entry(strategy)  # records the candidate
    candidate_id = read_jsonl(tmp_path / "candidates.jsonl")[0]["candidate_id"]
    write_assessment(tmp_path / "assessments.jsonl", candidate_id, decision="approve")

    assert confirm_entry(strategy) is True


def test_filter_mode_blocks_a_rejected_assessment(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    confirm_entry(strategy)
    candidate_id = read_jsonl(tmp_path / "candidates.jsonl")[0]["candidate_id"]
    write_assessment(tmp_path / "assessments.jsonl", candidate_id, decision="reject")

    assert confirm_entry(strategy) is False


def test_filter_mode_blocks_an_abstain_assessment(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    confirm_entry(strategy)
    candidate_id = read_jsonl(tmp_path / "candidates.jsonl")[0]["candidate_id"]
    write_assessment(tmp_path / "assessments.jsonl", candidate_id, decision="abstain")

    assert confirm_entry(strategy) is False


def test_filter_mode_blocks_an_expired_assessment(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter", ttl_minutes=1.0)
    confirm_entry(strategy)
    candidate_id = read_jsonl(tmp_path / "candidates.jsonl")[0]["candidate_id"]
    stale_available_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    write_assessment(
        tmp_path / "assessments.jsonl",
        candidate_id,
        decision="approve",
        available_at=stale_available_at,
    )

    assert confirm_entry(strategy) is False


def test_filter_mode_blocks_on_a_malformed_assessment_line(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    confirm_entry(strategy)
    assessments_path = tmp_path / "assessments.jsonl"
    assessments_path.write_text("{not valid json\n")

    assert confirm_entry(strategy) is False


def test_filter_mode_never_raises_and_blocks_on_internal_error(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    strategy.dp = RaisingDataProvider()

    # Must return False, not raise: Freqtrade's strategy_safe_wrapper would
    # otherwise default an uncaught exception here to True (allow entry).
    assert confirm_entry(strategy) is False


def test_filter_mode_uses_the_newest_assessment_for_a_candidate(tmp_path):
    strategy = make_strategy(tmp_path, mode="filter")
    confirm_entry(strategy)
    candidate_id = read_jsonl(tmp_path / "candidates.jsonl")[0]["candidate_id"]
    assessments_path = tmp_path / "assessments.jsonl"
    older = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    newer = datetime.now(UTC).isoformat()
    write_assessment(assessments_path, candidate_id, decision="reject", available_at=older)
    write_assessment(assessments_path, candidate_id, decision="approve", available_at=newer)

    assert confirm_entry(strategy) is True


def test_unknown_mode_blocks_entry(tmp_path):
    # A mistyped mode (e.g. "fitler") must not silently disable the filter.
    strategy = make_strategy(tmp_path, mode="bogus")
    assert confirm_entry(strategy) is False
    assert not (tmp_path / "candidates.jsonl").exists()


@pytest.mark.parametrize("mode", ["off", "shadow", "filter"])
def test_exit_signal_logic_is_unaffected_by_jev_mode(mode, tmp_path):
    # populate_exit_trend must behave identically to H1ChannelBreakout
    # regardless of jev mode, confirming exits are not touched anywhere.
    strategy = make_strategy(tmp_path, mode=mode)
    n = strategy.exit_channel + 1
    df = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC"),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
        }
    )
    df.loc[n - 1, "close"] = 99.0
    df.loc[n - 1, "low"] = 1.0

    out = strategy.populate_indicators(df.copy(), {"pair": "BTC/EUR"})
    out = strategy.populate_entry_trend(out, {"pair": "BTC/EUR"})
    out = strategy.populate_exit_trend(out, {"pair": "BTC/EUR"})

    assert out.loc[n - 1, "exit_long"] == 1
