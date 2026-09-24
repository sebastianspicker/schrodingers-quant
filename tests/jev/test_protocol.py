"""Tests for sq.jev.protocol: the stdlib-only file contract, and its use as a
shared contract between H1JevShadow (candidate producer / assessment reader)
and sq.jev.worker (assessment producer)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from H1JevShadow import (
    ASSESSMENTS_FILENAME as STRATEGY_ASSESSMENTS_FILENAME,
)
from H1JevShadow import (
    CANDIDATES_FILENAME as STRATEGY_CANDIDATES_FILENAME,
)
from H1JevShadow import (
    DEFAULT_DIR as STRATEGY_DEFAULT_DIR,
)
from H1JevShadow import H1JevShadow, _latest_assessment
from sq.jev import protocol
from sq.jev.providers import NullProvider
from sq.jev.worker import PROMPTS_DIR, load_prompt, process_once

# Computed from experiments/jev/prompts/v1.txt's original bytes (before this
# restructure moved the file to src/sq/jev/prompts/v1.txt) with
# hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]; hardcoded so a future
# accidental change to the prompt is caught here, not just downstream.
ORIGINAL_PROMPT_VERSION = "sha256:4c39ab3f63f6ac14"


# --- read_jsonl / append_jsonl -------------------------------------------


def test_append_jsonl_and_read_jsonl_round_trip(tmp_path):
    path = tmp_path / "records.jsonl"
    protocol.append_jsonl(path, {"a": 1})
    protocol.append_jsonl(path, {"b": 2})

    records = list(protocol.read_jsonl(path))
    assert records == [{"a": 1}, {"b": 2}]


def test_read_jsonl_missing_file_yields_nothing(tmp_path):
    assert list(protocol.read_jsonl(tmp_path / "missing.jsonl")) == []


def test_read_jsonl_lenient_skips_malformed_lines(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"ok": true}\n{not valid json\n{"ok": 2}\n')

    records = list(protocol.read_jsonl(path))

    assert records == [{"ok": True}, {"ok": 2}]


def test_read_jsonl_strict_raises_on_malformed_line(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"ok": true}\n{not valid json\n')

    with pytest.raises(json.JSONDecodeError):
        list(protocol.read_jsonl(path, strict=True))


# --- parse_utc -------------------------------------------------------


def test_parse_utc_none_and_empty():
    assert protocol.parse_utc(None) is None
    assert protocol.parse_utc("") is None


def test_parse_utc_naive_is_treated_as_utc():
    parsed = protocol.parse_utc("2026-01-01T12:00:00")
    assert parsed == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_parse_utc_aware_is_converted_to_utc():
    parsed = protocol.parse_utc("2026-01-01T12:00:00+02:00")
    assert parsed == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def test_parse_utc_invalid_string_returns_none():
    assert protocol.parse_utc("not-a-timestamp") is None


# --- prompt versioning -------------------------------------------------


def test_default_prompt_version_matches_the_original_file_hash():
    _, version = load_prompt(PROMPTS_DIR / "v1.txt")
    assert version == ORIGINAL_PROMPT_VERSION


# --- contract: H1JevShadow's filenames/dir match protocol's --------------


def test_strategy_filenames_and_dir_match_protocol():
    assert STRATEGY_CANDIDATES_FILENAME == protocol.CANDIDATES_FILENAME
    assert STRATEGY_ASSESSMENTS_FILENAME == protocol.ASSESSMENTS_FILENAME
    assert Path(STRATEGY_DEFAULT_DIR) == protocol.DEFAULT_DIR


def test_approve_is_a_decision_the_strategy_treats_as_approval():
    # H1JevShadow's filter mode approves an entry only when decision == "approve".
    assert {"approve"} <= set(protocol.DECISIONS)


# --- contract: a strategy-recorded candidate is consumed by the worker,
# and the worker's assessment is found by the strategy's own lookup --------


class FakeDataProvider:
    def __init__(self, dataframe: pd.DataFrame):
        self._dataframe = dataframe

    def get_analyzed_dataframe(self, pair: str, timeframe: str):
        return self._dataframe, "cached"


def _make_dataframe(signal_time: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [signal_time - timedelta(hours=4), signal_time],
            "close": [100.0, 101.0],
        }
    )


def test_worker_consumes_a_strategy_recorded_candidate_end_to_end(tmp_path):
    signal_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    strategy = H1JevShadow(
        {
            "stake_currency": "EUR",
            "jev": {"mode": "shadow", "dir": str(tmp_path)},
        }
    )
    strategy.dp = FakeDataProvider(_make_dataframe(signal_time))

    candidate = strategy._jev_build_candidate("BTC/EUR", 100.0, datetime.now(UTC))
    strategy._jev_record_candidate(candidate)

    candidates_path = tmp_path / protocol.CANDIDATES_FILENAME
    assessments_path = tmp_path / protocol.ASSESSMENTS_FILENAME
    cursor_path = tmp_path / "worker_cursor.txt"

    processed = process_once(
        candidates_path=candidates_path,
        assessments_path=assessments_path,
        cursor_path=cursor_path,
        provider=NullProvider(),
        prompt="ignored",
        prompt_version=ORIGINAL_PROMPT_VERSION,
    )
    assert processed == 1

    # The strategy's own assessment lookup must find what the worker wrote.
    found = _latest_assessment(assessments_path, candidate["candidate_id"])
    assert found is not None
    assert found["candidate_id"] == candidate["candidate_id"]
    # NullProvider always abstains: not an approval, but proof the file
    # contract round-trips between the strategy and the worker either way.
    assert found["decision"] == "abstain"
