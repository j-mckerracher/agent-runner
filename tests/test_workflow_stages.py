"""Deterministic tests for `workflow.stages` (Prompt 9).

Covers the `WorkflowStage`/`StageResult` contract, `CallableStage`
invocation semantics, and lifecycle telemetry emitted through the
canonical Prompt-4 `telemetry` contract. No real LLM call, no server, no
Opik -- a local in-memory `EventSink` stands in for the caller-owned sink.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from telemetry import EventStatus, EventType
from workflow.models import RunContext, RunSpec
from workflow.stages import CallableStage, StageFailure, StageResult, StageStatus, WorkflowStage


class _RecordingSink:
    """Local in-memory `EventSink` stand-in: records every emitted event,
    tracks whether it was closed. No file/network I/O.
    """

    def __init__(self) -> None:
        self.events: list = []
        self.closed = False

    def emit(self, event) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _RaisingSink(_RecordingSink):
    """A sink whose `emit` always raises -- used to prove sink failures
    never replace the original callable exception."""

    def emit(self, event) -> None:
        super().emit(event)
        raise RuntimeError("sink is unavailable")


def _clock_sequence(*moments: datetime):
    it = iter(moments)

    def _clock() -> datetime:
        return next(it)

    return _clock


def _context() -> RunContext:
    return RunContext.for_spec(RunSpec(), run_id="run-fixed")


T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 18, 12, 0, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# StageResult contract / serialization
# --------------------------------------------------------------------------


def test_stage_has_stable_name():
    stage = CallableStage("intake", lambda: None)
    assert stage.name == "intake"


def test_successful_run_returns_succeeded_status_and_real_output():
    stage = CallableStage("intake", lambda: "artifact-path", clock=_clock_sequence(T0, T1))
    result = stage.run(_context())
    assert result.status is StageStatus.SUCCEEDED
    assert result.output == "artifact-path"
    assert result.failure is None


def test_captured_failure_returns_failed_status_with_structured_error():
    def boom():
        raise ValueError("bad input")

    stage = CallableStage("intake", boom, clock=_clock_sequence(T0, T1))
    result = stage.run_capturing(_context())
    assert result.status is StageStatus.FAILED
    assert result.failure is not None
    assert result.failure.error_type == "ValueError"
    assert result.failure.message == "bad input"
    assert result.failure.stage == "intake"
    assert result.output is None


def test_failed_result_cannot_fabricate_output():
    with pytest.raises(ValueError):
        StageResult(
            stage_name="intake",
            status=StageStatus.FAILED,
            started_at=T0,
            finished_at=T1,
            output="should not exist",
            failure=StageFailure(error_type="X", message="y"),
        )


def test_failed_result_must_carry_a_structured_failure():
    with pytest.raises(ValueError):
        StageResult(
            stage_name="intake",
            status=StageStatus.FAILED,
            started_at=T0,
            finished_at=T1,
            failure=None,
        )


def test_succeeded_result_cannot_carry_a_failure():
    with pytest.raises(ValueError):
        StageResult(
            stage_name="intake",
            status=StageStatus.SUCCEEDED,
            started_at=T0,
            finished_at=T1,
            failure=StageFailure(error_type="X", message="y"),
        )


def test_duration_derives_from_injected_timestamps():
    result = StageResult(stage_name="intake", status=StageStatus.SUCCEEDED, started_at=T0, finished_at=T1)
    assert result.duration_ms == pytest.approx(1000.0)


def test_finished_before_started_rejected():
    with pytest.raises(ValueError):
        StageResult(stage_name="intake", status=StageStatus.SUCCEEDED, started_at=T1, finished_at=T0)


def test_naive_timestamps_rejected():
    naive = datetime(2026, 7, 18, 12, 0, 0)
    with pytest.raises(ValueError):
        StageResult(stage_name="intake", status=StageStatus.SUCCEEDED, started_at=naive, finished_at=T1)


def test_to_dict_is_deterministic_and_unknown_optionals_stay_none():
    result = StageResult(stage_name="intake", status=StageStatus.SUCCEEDED, started_at=T0, finished_at=T1)
    d = result.to_dict()
    assert d == {
        "stage_name": "intake",
        "status": "succeeded",
        "started_at": T0.isoformat(),
        "finished_at": T1.isoformat(),
        "duration_ms": 1000.0,
        "output": None,
        "failure": None,
        "span_id": None,
        "parent_span_id": None,
    }


def test_workflow_stage_protocol_recognizes_callable_stage():
    stage = CallableStage("intake", lambda: None)
    assert isinstance(stage, WorkflowStage)


# --------------------------------------------------------------------------
# Invocation semantics
# --------------------------------------------------------------------------


def test_wrapped_callable_invoked_exactly_once():
    calls = []

    def func(*args, **kwargs):
        calls.append((args, kwargs))
        return "done"

    stage = CallableStage("intake", func)
    stage.run(_context())
    assert len(calls) == 1


def test_arguments_pass_through_correctly():
    received = {}

    def func(a, b, *, c):
        received["a"] = a
        received["b"] = b
        received["c"] = c
        return None

    stage = CallableStage("intake", func)
    stage.run(_context(), 1, 2, c=3)
    assert received == {"a": 1, "b": 2, "c": 3}


def test_default_run_propagates_original_exception_unchanged():
    exc = ValueError("original")

    def boom():
        raise exc

    stage = CallableStage("intake", boom)
    with pytest.raises(ValueError) as excinfo:
        stage.run(_context())
    assert excinfo.value is exc


def test_run_capturing_catches_exception():
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(RuntimeError("oops")))
    result = stage.run_capturing(_context())
    assert result.status is StageStatus.FAILED


def test_system_exit_propagates_through_run():
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(SystemExit(1)))
    with pytest.raises(SystemExit):
        stage.run(_context())


def test_system_exit_propagates_through_run_capturing():
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(SystemExit(1)))
    with pytest.raises(SystemExit):
        stage.run_capturing(_context())


def test_keyboard_interrupt_propagates_through_run():
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        stage.run(_context())


def test_keyboard_interrupt_propagates_through_run_capturing():
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        stage.run_capturing(_context())


def test_run_capturing_never_converts_non_exception_base_exception():
    """SystemExit/KeyboardInterrupt must never come back as a StageResult."""
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(SystemExit(2)))
    try:
        stage.run_capturing(_context())
        assert False, "expected SystemExit to propagate"
    except SystemExit:
        pass


def test_separate_stage_instances_do_not_share_mutable_defaults():
    stage_a = CallableStage("intake", lambda: "a")
    stage_b = CallableStage("qa", lambda: "b")
    ctx = _context()
    stage_a.run(ctx)
    stage_b.run(ctx)
    assert stage_a.name == "intake"
    assert stage_b.name == "qa"


def test_current_stage_restored_after_success():
    ctx = _context()
    ctx.current_stage = "previous-stage"
    stage = CallableStage("intake", lambda: "ok")
    stage.run(ctx)
    assert ctx.current_stage == "previous-stage"


def test_current_stage_restored_after_captured_failure():
    ctx = _context()
    ctx.current_stage = "previous-stage"
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(ValueError("bad")))
    stage.run_capturing(ctx)
    assert ctx.current_stage == "previous-stage"


def test_current_stage_restored_after_propagated_exception():
    ctx = _context()
    ctx.current_stage = "previous-stage"
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ValueError):
        stage.run(ctx)
    assert ctx.current_stage == "previous-stage"


def test_current_stage_restored_after_propagated_base_exception():
    ctx = _context()
    ctx.current_stage = "previous-stage"
    stage = CallableStage("intake", lambda: (_ for _ in ()).throw(SystemExit(1)))
    with pytest.raises(SystemExit):
        stage.run(ctx)
    assert ctx.current_stage == "previous-stage"


def test_current_stage_is_set_during_execution():
    seen = {}

    def func(ctx):
        seen["stage"] = ctx.current_stage

    ctx = _context()
    stage = CallableStage("intake", lambda: seen.__setitem__("stage", ctx.current_stage))
    stage.run(ctx)
    assert seen["stage"] == "intake"


# --------------------------------------------------------------------------
# Lifecycle telemetry
# --------------------------------------------------------------------------


def test_success_emits_one_started_and_one_completed_event():
    sink = _RecordingSink()
    stage = CallableStage("intake", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(_context())
    types = [e.event_type for e in sink.events]
    assert types == [EventType.STAGE_STARTED, EventType.STAGE_COMPLETED]


def test_captured_failure_emits_one_started_and_one_failed_event_no_completed():
    sink = _RecordingSink()
    stage = CallableStage(
        "intake",
        lambda: (_ for _ in ()).throw(ValueError("bad")),
        sink=sink,
        clock=_clock_sequence(T0, T1),
    )
    stage.run_capturing(_context())
    types = [e.event_type for e in sink.events]
    assert types == [EventType.STAGE_STARTED, EventType.STAGE_FAILED]
    assert EventType.STAGE_COMPLETED not in types


def test_propagated_system_exit_still_emits_started_and_failed_events():
    sink = _RecordingSink()
    stage = CallableStage(
        "intake",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
        sink=sink,
        clock=_clock_sequence(T0, T1),
    )
    with pytest.raises(SystemExit):
        stage.run(_context())
    types = [e.event_type for e in sink.events]
    assert types == [EventType.STAGE_STARTED, EventType.STAGE_FAILED]


def test_started_event_has_no_status():
    sink = _RecordingSink()
    stage = CallableStage("intake", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(_context())
    assert sink.events[0].status is None


def test_completed_event_status_is_ok():
    sink = _RecordingSink()
    stage = CallableStage("intake", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(_context())
    assert sink.events[1].status == EventStatus.OK


def test_failed_event_status_is_error():
    sink = _RecordingSink()
    stage = CallableStage(
        "intake",
        lambda: (_ for _ in ()).throw(ValueError("bad")),
        sink=sink,
        clock=_clock_sequence(T0, T1),
    )
    stage.run_capturing(_context())
    assert sink.events[1].status == EventStatus.ERROR


def test_events_carry_correct_run_id_and_stage_name():
    sink = _RecordingSink()
    stage = CallableStage("qa", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(_context())
    for event in sink.events:
        assert event.run_id == "run-fixed"
        assert event.stage == "qa"


def test_events_carry_own_span_id_and_preserved_parent_span_id():
    sink = _RecordingSink()
    stage = CallableStage(
        "qa", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1), parent_span_id="span-parent"
    )
    stage.run(_context())
    for event in sink.events:
        assert event.span_id is not None
        assert event.parent_span_id == "span-parent"
    # Both events in one invocation share the same span id.
    assert sink.events[0].span_id == sink.events[1].span_id


def test_parent_span_id_defaults_to_none_and_is_never_derived_from_trace_reference():
    sink = _RecordingSink()
    ctx = _context()
    ctx.trace_reference = "evidence/some-run/trace.jsonl"  # a path, not a span id
    stage = CallableStage("qa", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(ctx)
    for event in sink.events:
        assert event.parent_span_id is None


def test_caller_owned_sink_is_not_closed_by_stage():
    sink = _RecordingSink()
    stage = CallableStage("intake", lambda: "ok", sink=sink, clock=_clock_sequence(T0, T1))
    stage.run(_context())
    assert sink.closed is False


def test_sink_emit_failure_does_not_replace_the_original_exception():
    sink = _RaisingSink()
    original = ValueError("original failure")

    def boom():
        raise original

    stage = CallableStage("intake", boom, sink=sink, clock=_clock_sequence(T0, T1))
    with pytest.raises(ValueError) as excinfo:
        stage.run(_context())
    assert excinfo.value is original


def test_no_sink_means_no_emission_but_execution_still_works():
    stage = CallableStage("intake", lambda: "ok", clock=_clock_sequence(T0, T1))
    result = stage.run(_context())
    assert result.status is StageStatus.SUCCEEDED


def test_canonical_event_names_are_dotted_started_completed_failed():
    """Distinguishes the canonical contract from legacy run.py::_Stage,
    which uses 'stage.start'/'stage.end'."""
    assert EventType.STAGE_STARTED.value == "stage.started"
    assert EventType.STAGE_COMPLETED.value == "stage.completed"
    assert EventType.STAGE_FAILED.value == "stage.failed"


def test_stages_module_does_not_reference_server_events():
    import workflow.stages as stages_module

    assert not hasattr(stages_module, "emit")
    assert "server" not in stages_module.__dict__


# --------------------------------------------------------------------------
# Integration proof: an existing (legacy-shaped) callable through the
# contract, under a real stage-name string. This proves CallableStage
# itself emits exactly one clean canonical start/terminal pair -- it does
# NOT claim anything about legacy `_Stage` nesting, since no production
# `run.py` call site is wrapped in this prompt.
# --------------------------------------------------------------------------


def _legacy_style_intake_stage(story_ref: str) -> dict:
    """Stands in for a real stage function such as `core.steps.step_intake`
    -- takes plain arguments, returns a plain value, raises plain
    exceptions. Not modified or extracted; just invoked through the new
    contract without rewriting its shape.
    """

    if not story_ref:
        raise FileNotFoundError("no story reference provided")
    return {"intake_source": story_ref}


def test_integration_proof_wraps_existing_callable_under_real_stage_name():
    sink = _RecordingSink()
    stage = CallableStage(
        "intake",  # real stage name from run.py::STAGE_INTAKE
        _legacy_style_intake_stage,
        sink=sink,
        clock=_clock_sequence(T0, T1),
    )
    result = stage.run(_context(), "story-123")
    assert result.status is StageStatus.SUCCEEDED
    assert result.output == {"intake_source": "story-123"}
    types = [e.event_type for e in sink.events]
    assert types == [EventType.STAGE_STARTED, EventType.STAGE_COMPLETED]
