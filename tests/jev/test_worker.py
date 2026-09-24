"""Tests for sq.jev.worker: cursor, dedupe, timeouts and the lock."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sq.jev import worker
from sq.jev.providers import Assessment


class ScriptedProvider:
    """Provider whose assess() is a test-supplied callable."""

    def __init__(self, fn):
        self._fn = fn

    def assess(self, candidate: dict, prompt: str) -> Assessment:
        return self._fn(candidate, prompt)


def approving_provider() -> ScriptedProvider:
    return ScriptedProvider(
        lambda candidate, prompt: Assessment(
            decision="approve", rationale="ok", confidence=0.9, model="test", model_version="v1"
        )
    )


def raising_provider(exc: type[Exception] = ValueError) -> ScriptedProvider:
    def _raise(candidate, prompt):
        raise exc("boom")

    return ScriptedProvider(_raise)


def slow_provider(delay_seconds: float) -> ScriptedProvider:
    def _slow(candidate, prompt):
        time.sleep(delay_seconds)
        return Assessment(
            decision="approve", rationale="ok", confidence=None, model="t", model_version="v1"
        )

    return ScriptedProvider(_slow)


def write_candidate(path: Path, candidate_id: str, pair: str = "BTC/EUR") -> dict:
    record = {
        "candidate_id": candidate_id,
        "pair": pair,
        "signal_candle_time": "2026-01-01T12:00:00+00:00",
        "rate": 100.0,
        "observed_at": datetime.now(UTC).isoformat(),
        "strategy_name": "H1JevShadow",
        "strategy_version": "test-version",
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def make_paths(tmp_path: Path) -> dict:
    return {
        "candidates_path": tmp_path / "candidates.jsonl",
        "assessments_path": tmp_path / "assessments.jsonl",
        "cursor_path": tmp_path / "cursor.txt",
    }


# --- prompt versioning -----------------------------------------------


def test_load_prompt_version_is_derived_from_content(tmp_path):
    prompt_path = tmp_path / "v1.txt"
    prompt_path.write_text("hello jev")
    text, version = worker.load_prompt(prompt_path)

    assert text == "hello jev"
    assert version.startswith("sha256:")

    prompt_path.write_text("hello jev, changed")
    _, changed_version = worker.load_prompt(prompt_path)
    assert changed_version != version


# --- process_once: basic processing, cursor, dedupe -----------------------


def test_process_once_processes_new_candidates_and_advances_cursor(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")
    write_candidate(paths["candidates_path"], "cand-2")

    processed = worker.process_once(
        **paths, provider=approving_provider(), prompt="p", prompt_version="sha256:v1"
    )

    assert processed == 2
    records = read_jsonl(paths["assessments_path"])
    assert {r["candidate_id"] for r in records} == {"cand-1", "cand-2"}
    for record in records:
        assert record["decision"] == "approve"
        assert record["error"] is None
        assert record["prompt_version"] == "sha256:v1"
        assert record["input"]["pair"] == "BTC/EUR"

    cursor_after_first_pass = worker.read_cursor(paths["cursor_path"])
    assert cursor_after_first_pass == paths["candidates_path"].stat().st_size


def test_process_once_is_idempotent_with_no_new_candidates(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")
    worker.process_once(**paths, provider=approving_provider(), prompt="p", prompt_version="v1")

    processed_again = worker.process_once(
        **paths, provider=approving_provider(), prompt="p", prompt_version="v1"
    )

    assert processed_again == 0
    assert len(read_jsonl(paths["assessments_path"])) == 1


def test_process_once_respects_the_cursor_across_passes(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")
    worker.process_once(**paths, provider=approving_provider(), prompt="p", prompt_version="v1")

    write_candidate(paths["candidates_path"], "cand-2")
    processed = worker.process_once(
        **paths, provider=approving_provider(), prompt="p", prompt_version="v1"
    )

    assert processed == 1
    assert {r["candidate_id"] for r in read_jsonl(paths["assessments_path"])} == {
        "cand-1",
        "cand-2",
    }


def test_process_once_never_requeries_a_candidate_even_if_cursor_is_reset(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")
    worker.process_once(**paths, provider=approving_provider(), prompt="p", prompt_version="v1")

    worker.write_cursor(paths["cursor_path"], 0)  # simulate a reset/corrupted cursor
    calls = []
    counting_provider = ScriptedProvider(
        lambda candidate, prompt: (
            calls.append(candidate)
            or Assessment(
                decision="approve", rationale="x", confidence=None, model="t", model_version="v1"
            )
        )
    )
    processed = worker.process_once(
        **paths, provider=counting_provider, prompt="p", prompt_version="v1"
    )

    assert processed == 0
    assert calls == []
    assert len(read_jsonl(paths["assessments_path"])) == 1


def test_process_once_respects_queue_maxsize(tmp_path):
    paths = make_paths(tmp_path)
    for i in range(5):
        write_candidate(paths["candidates_path"], f"cand-{i}")

    processed = worker.process_once(
        **paths, provider=approving_provider(), prompt="p", prompt_version="v1", queue_maxsize=2
    )

    assert processed == 2
    assert len(read_jsonl(paths["assessments_path"])) == 2


# --- error handling: provider exceptions and timeouts become abstains ----


def test_provider_exception_is_recorded_as_abstain_with_error(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")

    processed = worker.process_once(
        **paths,
        provider=raising_provider(ValueError),
        prompt="p",
        prompt_version="v1",
    )

    assert processed == 1
    record = read_jsonl(paths["assessments_path"])[0]
    assert record["decision"] == "abstain"
    assert record["error"] == "ValueError"


def test_provider_timeout_is_recorded_as_abstain_with_timeout_error(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")

    processed = worker.process_once(
        **paths,
        provider=slow_provider(0.3),
        prompt="p",
        prompt_version="v1",
        timeout_seconds=0.05,
    )

    assert processed == 1
    record = read_jsonl(paths["assessments_path"])[0]
    assert record["decision"] == "abstain"
    assert record["error"] == "TimeoutError"


def test_malformed_candidate_line_is_skipped_without_crashing(tmp_path):
    paths = make_paths(tmp_path)
    write_candidate(paths["candidates_path"], "cand-1")
    with open(paths["candidates_path"], "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")

    processed = worker.process_once(
        **paths, provider=approving_provider(), prompt="p", prompt_version="v1"
    )

    assert processed == 1
    assert len(read_jsonl(paths["assessments_path"])) == 1


# --- single-instance lock -------------------------------------------------


def test_single_instance_lock_blocks_a_second_concurrent_run(tmp_path):
    lock_path = tmp_path / "worker.lock"
    with worker.single_instance_lock(lock_path):
        assert lock_path.exists()
        with pytest.raises(RuntimeError):
            with worker.single_instance_lock(lock_path):
                pass


def test_single_instance_lock_is_reusable_after_release(tmp_path):
    lock_path = tmp_path / "worker.lock"
    with worker.single_instance_lock(lock_path):
        pass
    with worker.single_instance_lock(lock_path):
        pass  # a second, non-overlapping run must succeed


def test_stale_lock_file_from_a_crashed_run_does_not_block_restart(tmp_path):
    # A crash or `docker kill` leaves the file behind but the kernel drops the lock.
    lock_path = tmp_path / "worker.lock"
    lock_path.write_text("12345")
    with worker.single_instance_lock(lock_path):
        assert lock_path.read_text() == str(os.getpid())
