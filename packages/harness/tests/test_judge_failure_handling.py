"""Tests for judge failure handling (pG-judge-failures)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_runner_harness.grading.rubric import JudgeConfig, grade_rubric
from agent_runner_shared.models import AcceptanceCriterion


def _make_rubric_criterion(
    id: str = "c1",
    description: str = "Some rubric criterion",
    threshold: float = 2.0,
    scale: str = "0-3",
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        id=id,
        description=description,
        kind="rubric",
        threshold=threshold,
        scale=scale,
    )


class TestInvalidJsonJudge:
    def test_invalid_json_string_recorded_as_error(self, tmp_path: Path) -> None:
        """Judge returning a non-dict object is recorded as judge_error/invalid_json."""
        def bad_judge(prompt: str, rubric: str) -> Any:
            # Returns a string instead of dict — simulates invalid schema
            return "this is not a dict"  # type: ignore[return-value]

        criteria = [_make_rubric_criterion()]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=bad_judge,
        )
        assert len(results) == 1
        r = results[0]
        assert r["status"] == "judge_error"
        assert r["error_kind"] == "invalid_json"
        assert r["passed"] is False
        assert r["score"] is None

    def test_missing_score_field_recorded_as_error(self, tmp_path: Path) -> None:
        """Judge returning dict without 'score' is invalid_json."""
        def bad_judge(prompt: str, rubric: str) -> dict[str, Any]:
            return {"rationale": "looks good", "evidence_refs": []}  # missing score

        criteria = [_make_rubric_criterion()]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=bad_judge,
        )
        assert results[0]["status"] == "judge_error"
        assert results[0]["error_kind"] == "invalid_json"
        assert results[0]["score"] is None

    def test_missing_rationale_field_recorded_as_error(self, tmp_path: Path) -> None:
        """Judge returning dict without 'rationale' is invalid_json."""
        def bad_judge(prompt: str, rubric: str) -> dict[str, Any]:
            return {"score": 2}  # missing rationale

        criteria = [_make_rubric_criterion()]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=bad_judge,
        )
        assert results[0]["status"] == "judge_error"
        assert results[0]["error_kind"] == "invalid_json"


class TestTimeoutJudge:
    def test_timeout_recorded_as_error(self, tmp_path: Path) -> None:
        """Judge that sleeps longer than timeout is recorded as timeout error."""
        def slow_judge(prompt: str, rubric: str) -> dict[str, Any]:
            time.sleep(2.0)
            return {"score": 3, "rationale": "ok", "evidence_refs": []}

        config = JudgeConfig(timeout_seconds=0.1, max_retries=0)
        criteria = [_make_rubric_criterion()]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=slow_judge,
            judge_config=config,
        )
        assert len(results) == 1
        r = results[0]
        assert r["status"] == "judge_error"
        assert r["error_kind"] == "timeout"
        assert r["passed"] is False
        assert r["score"] is None


class TestTransportError:
    def test_exception_recorded_as_transport_error(self, tmp_path: Path) -> None:
        """Judge raising an exception is recorded as transport error."""
        def failing_judge(prompt: str, rubric: str) -> dict[str, Any]:
            raise ConnectionError("upstream unavailable")

        config = JudgeConfig(timeout_seconds=5.0, max_retries=0)
        criteria = [_make_rubric_criterion()]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=failing_judge,
            judge_config=config,
        )
        assert results[0]["status"] == "judge_error"
        assert results[0]["error_kind"] == "transport"
        assert results[0]["score"] is None


class TestGradingRecordAfterErrors:
    def test_grade_returns_results_even_all_errors(self, tmp_path: Path) -> None:
        """grade_rubric returns a complete list even if all criteria error."""
        def bad_judge(prompt: str, rubric: str) -> Any:
            return "not a dict"  # type: ignore[return-value]

        criteria = [
            _make_rubric_criterion("c1"),
            _make_rubric_criterion("c2"),
        ]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=bad_judge,
        )
        assert len(results) == 2
        assert all(r["status"] == "judge_error" for r in results)

    def test_judge_errors_count_is_surfaceable(self, tmp_path: Path) -> None:
        """judge_errors count can be computed from rubric result list."""
        def bad_judge(prompt: str, rubric: str) -> Any:
            return "bad"  # type: ignore[return-value]

        criteria = [
            _make_rubric_criterion("c1"),
            _make_rubric_criterion("c2"),
        ]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=bad_judge,
        )
        judge_errors = sum(1 for r in results if r.get("status") == "judge_error")
        assert judge_errors == 2

    def test_successful_criterion_not_affected_by_other_error(self, tmp_path: Path) -> None:
        """A passing criterion is unaffected when another criterion errors."""
        call_count = [0]

        def mixed_judge(prompt: str, rubric: str) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return "bad"  # type: ignore[return-value]
            return {"score": 3, "rationale": "perfect", "evidence_refs": []}

        criteria = [
            _make_rubric_criterion("c1", threshold=2.0),
            _make_rubric_criterion("c2", threshold=2.0),
        ]
        results = grade_rubric(
            criteria=criteria,
            artifact_dir=tmp_path,
            judge_fn=mixed_judge,
        )
        assert results[0]["status"] == "judge_error"
        assert results[1].get("status") != "judge_error"
        assert results[1]["passed"] is True
