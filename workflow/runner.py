"""Prompt 8 — WorkflowRunner Shell: the delegating runner.

`WorkflowRunner` is a thin shell: `RunSpec -> WorkflowRunner -> legacy
workflow callable -> WorkflowResult`. By default the legacy callable
invokes `run.py::main` (imported lazily, only when actually invoked, so
importing this module/package never pulls in `run`, `server`, or
`opik`).

Behavior preservation is the point of this module:

* `run()` calls the adapter exactly once and lets any exception raised
  by `run.py::main` propagate unchanged — this matches `run.py::main`'s
  own contract (it re-raises on failure; `SystemExit` is never
  swallowed). Propagation is the *default*, not an opt-in.
* `run_capturing()` is the explicit, separate opt-in for callers that
  want a structured `FAILED` result instead of a raised exception. It
  only catches `Exception`, never `BaseException` — `SystemExit`/
  `KeyboardInterrupt` still propagate.
* No lifecycle telemetry events are emitted here. `run.py::main` already
  owns its own trace emission; this shell does not duplicate it.
* No trace sink is constructed or closed by this module.
"""

from __future__ import annotations

from typing import Any, Callable

from .models import FailureDetail, RunContext, RunSpec, RunStatus, WorkflowResult, _utcnow

LegacyWorkflowCallable = Callable[[RunSpec, RunContext], Any]


def _default_legacy_workflow(spec: RunSpec, context: RunContext) -> Any:
    """Adapter that delegates to `run.py::main`.

    Lazily imports `run` so that importing `workflow` (or constructing a
    `WorkflowRunner` with a fake callable, as tests do) never touches
    `run.py`'s module-level side effects.
    """

    import run as legacy_run  # local import: preserves import isolation

    return legacy_run.main(
        repo=str(spec.repo_path) if spec.repo_path else None,
        change_id=spec.change_id,
        ado_url=spec.ado_url,
        story_file=str(spec.story_path) if spec.story_path else None,
        manual_story_file=str(spec.manual_story_path) if spec.manual_story_path else None,
        runner=spec.runner,
        model=spec.model,
        agent_llm_overrides=spec.agent_llm_overrides,
        extra_context=spec.extra_context,
        skip_lessons_optimizer=spec.skip_lessons_optimizer,
        skip_materialize=spec.skip_materialize,
        calibration_fast_mode=spec.calibration_fast_mode,
        headless=spec.headless,
        log_level=spec.log_level,
    )


class WorkflowRunner:
    """Delegates a `RunSpec` to a legacy workflow callable exactly once."""

    def __init__(self, *, legacy_workflow: LegacyWorkflowCallable | None = None) -> None:
        self._legacy_workflow = legacy_workflow or _default_legacy_workflow

    def run(self, spec: RunSpec) -> WorkflowResult:
        """Run `spec`, propagating any exception raised by the adapter."""

        context = RunContext.for_spec(spec)
        value = self._legacy_workflow(spec, context)
        finished_at = _utcnow()
        return WorkflowResult(
            run_id=context.run_id,
            status=RunStatus.SUCCEEDED,
            started_at=context.started_at,
            finished_at=finished_at,
            final_output=value,
            trace_reference=context.trace_reference,
            artifact_references=context.artifact_references,
            runtime_metadata=dict(context.runtime_metadata),
        )

    def run_capturing(self, spec: RunSpec) -> WorkflowResult:
        """Run `spec`, converting an `Exception` into a FAILED result.

        `BaseException` subclasses that are not `Exception` (notably
        `SystemExit`, `KeyboardInterrupt`) still propagate, matching
        `run.py::main`'s own handling of `SystemExit`.
        """

        context = RunContext.for_spec(spec)
        try:
            value = self._legacy_workflow(spec, context)
        except Exception as exc:  # noqa: BLE001 - intentional broad capture, opt-in only
            finished_at = _utcnow()
            failure = FailureDetail(
                error_type=type(exc).__name__,
                message=str(exc),
                stage=context.current_stage,
                propagated=False,
            )
            return WorkflowResult(
                run_id=context.run_id,
                status=RunStatus.FAILED,
                started_at=context.started_at,
                finished_at=finished_at,
                failure=failure,
                trace_reference=context.trace_reference,
                artifact_references=context.artifact_references,
                runtime_metadata=dict(context.runtime_metadata),
            )

        finished_at = _utcnow()
        return WorkflowResult(
            run_id=context.run_id,
            status=RunStatus.SUCCEEDED,
            started_at=context.started_at,
            finished_at=finished_at,
            final_output=value,
            trace_reference=context.trace_reference,
            artifact_references=context.artifact_references,
            runtime_metadata=dict(context.runtime_metadata),
        )
