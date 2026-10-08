"""Tests for `sq.jev.protocol` (stdlib-only JSONL contract)."""

import json
import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sq.jev import protocol


def test_constants():
    assert protocol.DECISIONS == ("approve", "reject", "abstain")
    assert protocol.CANDIDATES_FILENAME == "candidates.jsonl"
    assert protocol.ASSESSMENTS_FILENAME == "assessments.jsonl"


def test_append_and_read_round_trip(tmp_path):
    path = tmp_path / "candidates.jsonl"
    records = [{"id": "a", "n": 1}, {"id": "b", "nested": {"x": [1, 2]}}, {"id": "ü"}]
    for record in records:
        protocol.append_jsonl(path, record)
    assert list(protocol.read_jsonl(path)) == records
    # One sorted-key line per record.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0] == json.dumps({"id": "a", "n": 1}, sort_keys=True)


def test_append_sorts_keys(tmp_path):
    path = tmp_path / "f.jsonl"
    protocol.append_jsonl(path, {"b": 1, "a": 2})
    assert path.read_text(encoding="utf-8") == '{"a": 2, "b": 1}\n'


def test_read_missing_file_yields_nothing(tmp_path):
    assert list(protocol.read_jsonl(tmp_path / "absent.jsonl")) == []


def test_read_skips_blank_and_malformed_lines(tmp_path, caplog):
    path = tmp_path / "f.jsonl"
    path.write_text('{"a": 1}\n\n   \nnot json\n{"b": 2}\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=protocol.logger.name):
        assert list(protocol.read_jsonl(path)) == [{"a": 1}, {"b": 2}]
    assert "malformed line 4" in caplog.text


def test_read_strict_raises_on_malformed_line(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        list(protocol.read_jsonl(path, strict=True))


def test_parse_utc_aware_values_are_converted():
    assert protocol.parse_utc("2026-01-02T03:04:05+00:00") == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )
    parsed = protocol.parse_utc("2026-01-02T03:04:05+02:00")
    assert parsed == datetime(2026, 1, 2, 1, 4, 5, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)
    assert protocol.parse_utc("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_utc_naive_is_assumed_utc():
    parsed = protocol.parse_utc("2026-01-02T03:04:05")
    assert parsed == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "not a date", "2026-13-45"])
def test_parse_utc_invalid_returns_none(value):
    assert protocol.parse_utc(value) is None


def test_parse_utc_non_string_returns_none():
    assert protocol.parse_utc(12345) is None  # type: ignore[arg-type]


def test_parse_utc_round_trips_isoformat():
    now = datetime(2026, 5, 6, 7, 8, 9, 123456, tzinfo=timezone(timedelta(hours=-5)))
    assert protocol.parse_utc(now.isoformat()) == now
