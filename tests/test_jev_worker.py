"""Provider response contract and worker progress across malformed answers."""

from dataclasses import replace

import pytest

from sq.jev.protocol import append_jsonl, read_jsonl
from sq.jev.providers import Assessment
from sq.jev.worker import process_once

VALID = Assessment("approve", "Recorded rationale", 0.5, "example", "v1")


def run_answers(tmp_path, answers):
    answers = iter(answers)

    class Provider:
        def assess(self, candidate, prompt):
            return next(answers)

    candidates = tmp_path / "candidates.jsonl"
    for candidate_id in ("first", "second"):
        append_jsonl(candidates, {"candidate_id": candidate_id})
    assessments = tmp_path / "assessments.jsonl"
    count = process_once(
        candidates_path=candidates,
        assessments_path=assessments,
        cursor_path=tmp_path / "cursor.txt",
        provider=Provider(),
        prompt="Test prompt",
        prompt_version="test-v1",
    )
    assert count == 2
    assert (tmp_path / "cursor.txt").read_text().strip() == str(candidates.stat().st_size)
    return list(read_jsonl(assessments))


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {"decision": "approve"},
        replace(VALID, rationale=[]),
        replace(VALID, rationale={"text": "approve"}),
        replace(VALID, model=[]),
        replace(VALID, model_version=None),
        replace(VALID, confidence="high"),
        replace(VALID, confidence=True),
    ],
)
def test_malformed_answer_abstains_and_next_candidate_proceeds(tmp_path, answer):
    first, second = run_answers(tmp_path, [answer, VALID])
    assert first["decision"] == "abstain"
    assert first["error"] == "InvalidAssessment"
    assert first["model"] == "unknown"
    assert isinstance(first["rationale"], str)
    assert second["decision"] == "approve"
    assert second["error"] is None


def test_nested_rationale_is_rejected_independently_of_json_recursion_limits(tmp_path):
    nested = []
    for _ in range(2000):
        nested = [nested]
    first, second = run_answers(tmp_path, [replace(VALID, rationale=nested), VALID])
    assert first["decision"] == "abstain"
    assert first["error"] == "InvalidAssessment"
    assert second["decision"] == "approve"


@pytest.mark.parametrize("decision", ["approve", "reject", "abstain"])
@pytest.mark.parametrize("confidence", [None, 0, 0.5, 2.0])
def test_valid_fields_preserve_decisions_without_inventing_confidence_bounds(
    tmp_path, decision, confidence
):
    answer = replace(VALID, decision=decision, confidence=confidence)
    for record in run_answers(tmp_path, [answer, answer]):
        assert record["decision"] == decision
        assert record["confidence"] == confidence
        assert record["rationale"] == VALID.rationale
        assert record["error"] is None


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_confidence_still_abstains(tmp_path, confidence):
    first, second = run_answers(tmp_path, [replace(VALID, confidence=confidence), VALID])
    assert first["decision"] == "abstain"
    assert first["error"] == "UnserialisableAssessment"
    assert second["decision"] == "approve"


def test_unknown_decision_retains_existing_diagnostic(tmp_path):
    first, second = run_answers(tmp_path, [replace(VALID, decision="APPROVE!"), VALID])
    assert first["decision"] == "abstain"
    assert first["error"] == "InvalidDecision"
    assert second["decision"] == "approve"
