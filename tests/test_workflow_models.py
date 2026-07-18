"""Deterministic tests for `workflow.models` (Prompt 8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workflow.models import FailureDetail, RunContext, RunSpec, RunStatus, WorkflowResult


# --------------------------------------------------------------------------
# RunSpec
# --------------------------------------------------------------------------


def test_run_spec_minimal_construction_has_expected_defaults():
    spec = RunSpec()
    assert spec.repo_path is None
    assert spec.change_id is None
    assert spec.ado_url is None
    assert spec.story_path is None
    assert spec.manual_story_path is None
    assert spec.runner == "claude"
    assert spec.model is None
    assert spec.skip_lessons_optimizer is True
    assert spec.skip_materialize is True
    assert spec.calibration_fast_mode is False
    assert spec.headless is False
    assert spec.log_level == "warning"


def test_run_spec_coerces_str_paths_to_path():
    spec = RunSpec(repo_path="some/repo", story_path="some/story.json")
    assert spec.repo_path == Path("some/repo")
    assert spec.story_path == Path("some/story.json")


def test_run_spec_accepts_path_objects_unchanged():
    repo = Path("some/repo")
    spec = RunSpec(repo_path=repo)
    assert spec.repo_path == repo


def test_run_spec_allows_no_story_source_at_all():
    """Legacy `resolve_workflow_input` silently defaults to a synthetic
    fixture when none of ado_url/story_file/manual_story_file are given --
    it does not raise. RunSpec must match that, not invent a stricter rule.
    """

    spec = RunSpec()  # must not raise
    assert spec.ado_url is None
    assert spec.story_path is None
    assert spec.manual_story_path is None


def test_run_spec_allows_exactly_one_story_source():
    RunSpec(ado_url="https://dev.azure.com/org/proj/_workitems/edit/123")
    RunSpec(story_path="story.json")
    RunSpec(manual_story_path="manual.json")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ado_url": "https://dev.azure.com/x", "story_path": "s.json"},
        {"ado_url": "https://dev.azure.com/x", "manual_story_path": "m.json"},
        {"story_path": "s.json", "manual_story_path": "m.json"},
        {
            "ado_url": "https://dev.azure.com/x",
            "story_path": "s.json",
            "manual_story_path": "m.json",
        },
    ],
)
def test_run_spec_rejects_more_than_one_story_source(kwargs):
    with pytest.raises(ValueError, match="Provide only one of ado_url, story_path, or manual_story_path"):
        RunSpec(**kwargs)


def test_run_spec_is_frozen():
    spec = RunSpec()
    with pytest.raises((AttributeError, TypeError)):
        spec.runner = "codex"  # type: ignore[misc]


# --------------------------------------------------------------------------
# RunContext
# --------------------------------------------------------------------------


def test_run_context_for_spec_generates_unique_run_ids():
    spec = RunSpec()
    ctx_a = RunContext.for_spec(spec)
    ctx_b = RunContext.for_spec(spec)
    assert ctx_a.run_id != ctx_b.run_id


def test_run_context_for_spec_accepts_injected_run_id():
    spec = RunSpec()
    ctx = RunContext.for_spec(spec, run_id="fixed-run-id")
    assert ctx.run_id == "fixed-run-id"


def test_run_context_for_spec_sets_started_at_as_aware_utc_datetime():
    ctx = RunContext.for_spec(RunSpec())
    assert ctx.started_at.tzinfo is not None
    assert ctx.started_at.tzinfo.utcoffset(ctx.started_at) == timedelta(0)


def test_run_context_for_spec_accepts_injected_started_at():
    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ctx = RunContext.for_spec(RunSpec(), started_at=fixed)
    assert ctx.started_at == fixed


def test_run_context_defaults_have_no_leakage_between_instances():
    spec = RunSpec()
    ctx_a = RunContext.for_spec(spec)
    ctx_b = RunContext.for_spec(spec)
    ctx_a.runtime_metadata["k"] = "v"
    assert ctx_b.runtime_metadata == {}
    assert ctx_a.artifact_references == ()
    assert ctx_b.artifact_references == ()


def test_run_context_carries_the_spec_it_was_built_for():
    spec = RunSpec(runner="codex")
    ctx = RunContext.for_spec(spec)
    assert ctx.spec is spec


# --------------------------------------------------------------------------
# FailureDetail / WorkflowResult
# --------------------------------------------------------------------------


def _fixed_times():
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    finished = started + timedelta(milliseconds=1500)
    return started, finished


def test_workflow_result_succeeded_shape():
    started, finished = _fixed_times()
    result = WorkflowResult(
        run_id="run-1",
        status=RunStatus.SUCCEEDED,
        started_at=started,
        finished_at=finished,
        final_output="intake-source.json",
    )
    assert result.status is RunStatus.SUCCEEDED
    assert result.failure is None
    assert result.final_output == "intake-source.json"
    assert result.duration_ms == pytest.approx(1500.0)
    assert result.trace_reference is None
    assert result.artifact_references == ()


def test_workflow_result_failed_shape_with_failure_detail():
    started, finished = _fixed_times()
    failure = FailureDetail(error_type="ValueError", message="boom", stage=None, propagated=False)
    result = WorkflowResult(
        run_id="run-2",
        status=RunStatus.FAILED,
        started_at=started,
        finished_at=finished,
        failure=failure,
    )
    assert result.status is RunStatus.FAILED
    assert result.final_output is None
    assert result.failure is failure
    assert result.failure.propagated is False


def test_workflow_result_unavailable_fields_stay_none_not_fabricated():
    started, finished = _fixed_times()
    result = WorkflowResult(
        run_id="run-3",
        status=RunStatus.SUCCEEDED,
        started_at=started,
        finished_at=finished,
    )
    assert result.trace_reference is None
    assert result.artifact_references == ()
    assert result.runtime_metadata == {}


def test_workflow_result_to_dict_is_deterministic_and_json_shaped():
    started, finished = _fixed_times()
    failure = FailureDetail(error_type="RuntimeError", message="x", stage="s", propagated=True)
    result = WorkflowResult(
        run_id="run-4",
        status=RunStatus.FAILED,
        started_at=started,
        finished_at=finished,
        failure=failure,
        artifact_references=("a.txt", "b.txt"),
        runtime_metadata={"k": "v"},
    )
    payload = result.to_dict()
    assert payload["run_id"] == "run-4"
    assert payload["status"] == "failed"
    assert payload["started_at"] == started.isoformat()
    assert payload["finished_at"] == finished.isoformat()
    assert payload["failure"] == {
        "error_type": "RuntimeError",
        "message": "x",
        "stage": "s",
        "propagated": True,
    }
    assert payload["artifact_references"] == ["a.txt", "b.txt"]
    assert payload["runtime_metadata"] == {"k": "v"}


def test_failure_detail_to_dict_shape():
    failure = FailureDetail(error_type="ValueError", message="bad input", stage="intake", propagated=True)
    assert failure.to_dict() == {
        "error_type": "ValueError",
        "message": "bad input",
        "stage": "intake",
        "propagated": True,
    }


def test_run_status_values_match_legacy_workflow_constants():
    assert RunStatus.SUCCEEDED.value == "succeeded"
    assert RunStatus.FAILED.value == "failed"
