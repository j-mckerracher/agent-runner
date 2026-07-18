"""Deterministic tests for `workflow.runner.WorkflowRunner` (Prompt 8).

No real LLM calls, no subprocesses, no `run.py` orchestration executes
in these tests except the default-adapter arg-mapping test, which
patches `run.main` with a recorder so no real workflow runs.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from workflow.models import RunSpec, RunStatus
from workflow.runner import WorkflowRunner, _default_legacy_workflow


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, spec, context):
        self.calls.append((spec, context))
        return "recorded-value"


# --------------------------------------------------------------------------
# run() -- default behavior: propagate exceptions
# --------------------------------------------------------------------------


def test_run_calls_fake_legacy_workflow_exactly_once_with_spec_and_context():
    fake = _Recorder()
    runner = WorkflowRunner(legacy_workflow=fake)
    spec = RunSpec(runner="claude")

    result = runner.run(spec)

    assert len(fake.calls) == 1
    called_spec, called_context = fake.calls[0]
    assert called_spec is spec
    assert called_context.spec is spec
    assert result.final_output == "recorded-value"
    assert result.status is RunStatus.SUCCEEDED


def test_run_success_produces_succeeded_result_with_run_id_from_context():
    fake = _Recorder()
    runner = WorkflowRunner(legacy_workflow=fake)
    result = runner.run(RunSpec())

    _, called_context = fake.calls[0]
    assert result.run_id == called_context.run_id
    assert result.finished_at >= result.started_at


def test_run_propagates_exception_by_default():
    def _raises(spec, context):
        raise ValueError("legacy failure")

    runner = WorkflowRunner(legacy_workflow=_raises)
    with pytest.raises(ValueError, match="legacy failure"):
        runner.run(RunSpec())


def test_run_propagates_system_exit():
    def _raises(spec, context):
        raise SystemExit(1)

    runner = WorkflowRunner(legacy_workflow=_raises)
    with pytest.raises(SystemExit):
        runner.run(RunSpec())


def test_run_two_calls_get_distinct_contexts_and_run_ids():
    fake = _Recorder()
    runner = WorkflowRunner(legacy_workflow=fake)
    spec = RunSpec()

    result_a = runner.run(spec)
    result_b = runner.run(spec)

    assert result_a.run_id != result_b.run_id
    assert fake.calls[0][1] is not fake.calls[1][1]


# --------------------------------------------------------------------------
# run_capturing() -- explicit opt-in for a structured FAILED result
# --------------------------------------------------------------------------


def test_run_capturing_success_produces_succeeded_result():
    fake = _Recorder()
    runner = WorkflowRunner(legacy_workflow=fake)
    result = runner.run_capturing(RunSpec())
    assert result.status is RunStatus.SUCCEEDED
    assert result.final_output == "recorded-value"
    assert result.failure is None


def test_run_capturing_converts_exception_to_failed_result_not_propagated():
    def _raises(spec, context):
        raise ValueError("legacy failure")

    runner = WorkflowRunner(legacy_workflow=_raises)
    result = runner.run_capturing(RunSpec())

    assert result.status is RunStatus.FAILED
    assert result.final_output is None
    assert result.failure is not None
    assert result.failure.error_type == "ValueError"
    assert result.failure.message == "legacy failure"
    assert result.failure.propagated is False


def test_run_capturing_still_propagates_system_exit():
    def _raises(spec, context):
        raise SystemExit(1)

    runner = WorkflowRunner(legacy_workflow=_raises)
    with pytest.raises(SystemExit):
        runner.run_capturing(RunSpec())


def test_run_capturing_still_propagates_keyboard_interrupt():
    def _raises(spec, context):
        raise KeyboardInterrupt()

    runner = WorkflowRunner(legacy_workflow=_raises)
    with pytest.raises(KeyboardInterrupt):
        runner.run_capturing(RunSpec())


# --------------------------------------------------------------------------
# Default adapter: kwargs translate correctly, no real orchestration runs
# --------------------------------------------------------------------------


def test_default_adapter_maps_spec_fields_to_run_main_kwargs():
    spec = RunSpec(
        repo_path="some/repo",
        change_id="12345",
        story_path="some/story.json",
        runner="codex",
        model="gpt-5",
        extra_context="extra",
        skip_lessons_optimizer=False,
        skip_materialize=False,
        calibration_fast_mode=True,
        headless=True,
        log_level="debug",
    )

    with patch("run.main", return_value="intake-source") as mock_main:
        result = _default_legacy_workflow(spec, context=None)

    mock_main.assert_called_once_with(
        repo="some/repo",
        change_id="12345",
        ado_url=None,
        story_file="some/story.json",
        manual_story_file=None,
        runner="codex",
        model="gpt-5",
        agent_llm_overrides=None,
        extra_context="extra",
        skip_lessons_optimizer=False,
        skip_materialize=False,
        calibration_fast_mode=True,
        headless=True,
        log_level="debug",
    )
    assert result == "intake-source"


def test_default_adapter_passes_none_for_unset_path_fields():
    spec = RunSpec()
    with patch("run.main", return_value="ok") as mock_main:
        _default_legacy_workflow(spec, context=None)

    _, kwargs = mock_main.call_args
    assert kwargs["repo"] is None
    assert kwargs["story_file"] is None
    assert kwargs["manual_story_file"] is None


# --------------------------------------------------------------------------
# End-to-end smoke: RunSpec -> WorkflowRunner -> seam -> WorkflowResult
# --------------------------------------------------------------------------


def test_smoke_run_spec_through_runner_to_workflow_result():
    fake = _Recorder()
    runner = WorkflowRunner(legacy_workflow=fake)
    spec = RunSpec(runner="claude", story_path="workflow-fixtures/synthetic_story.json")

    result = runner.run(spec)

    assert result.status is RunStatus.SUCCEEDED
    assert result.final_output == "recorded-value"
    assert result.run_id
    assert result.duration_ms >= 0
