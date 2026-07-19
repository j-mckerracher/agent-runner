"""Prompt 9 — Explicit Workflow Stage Contracts.

`WorkflowStage` / `StageResult` give an individual workflow stage a stable
identity, an execution boundary, structured status, deterministic timing,
output, and structured failure information — without rewriting any stage's
internals, without changing stage order, and without touching `run.py`'s
production `_Stage` call sites.

    result = stage.run(context, *args, **kwargs)

`CallableStage` is the delegating adapter: it wraps an existing callable
exactly as-is and executes it exactly once per `run`/`run_capturing` call.
Lifecycle events (`stage.started` / `stage.completed` / `stage.failed`) are
emitted through the canonical Prompt-4 `telemetry` contract (`make_event`,
`EventType`, `EventStatus`) via a caller-supplied `EventSink` — the stage
never constructs, owns, or closes that sink.

This module deliberately does NOT:

* import `run`, `server`, or `opik` at module import time (or ever);
* infer trace/span correlation from `RunContext.trace_reference` — that
  field is an evidence/reference locator, not a span id. `parent_span_id`
  is only ever what a caller explicitly passes to `CallableStage`;
* wrap any production `run.py::_Stage` call site (see `docs/workflow-stages.md`
  for the deferred Prompt 10 integration seam);
* add `stage_results` to `WorkflowResult` (nothing here honestly populates
  it through the current `run.py::main` path).

See `docs/workflow-stages.md` for the full contract writeup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from telemetry import EventSink, EventStatus, EventType, make_event, new_span_id

from .models import RunContext


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StageStatus(str, Enum):
    """Terminal status of a single stage execution.

    Distinct from `telemetry.EventStatus` ("ok"/"error", which describes a
    telemetry event) and distinct from `run.py`'s legacy `STATUS_OK`/
    `STATUS_ERROR` rendering constants — this is stage-result vocabulary.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class StageFailure:
    """Structured description of a stage execution that raised an exception."""

    error_type: str
    message: str
    stage: str | None = None
    propagated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "stage": self.stage,
            "propagated": self.propagated,
        }


@dataclass
class StageResult:
    """Structured output of one `WorkflowStage.run`/`run_capturing` call.

    Invariants (enforced in `__post_init__`, raise `ValueError` otherwise):

    * `SUCCEEDED` results carry no `failure`.
    * `FAILED` results always carry a `failure` and never fabricate `output`.
    * `started_at`/`finished_at` must be timezone-aware, and `finished_at`
      may not precede `started_at`.
    """

    stage_name: str
    status: StageStatus
    started_at: datetime
    finished_at: datetime
    output: Any | None = None
    failure: StageFailure | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None:
            raise ValueError("StageResult.started_at must be timezone-aware")
        if self.finished_at.tzinfo is None:
            raise ValueError("StageResult.finished_at must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("StageResult.finished_at cannot precede started_at")
        if self.status is StageStatus.SUCCEEDED and self.failure is not None:
            raise ValueError("A SUCCEEDED StageResult cannot carry a failure")
        if self.status is StageStatus.FAILED:
            if self.failure is None:
                raise ValueError("A FAILED StageResult must carry a structured failure")
            if self.output is not None:
                raise ValueError("A FAILED StageResult cannot fabricate output")

    @property
    def duration_ms(self) -> float:
        delta = self.finished_at - self.started_at
        return delta.total_seconds() * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "output": self.output,
            "failure": self.failure.to_dict() if self.failure else None,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
        }


@runtime_checkable
class WorkflowStage(Protocol):
    """Contract for anything that can execute one workflow stage.

    A stable `name` plus a single execution method that always returns a
    `StageResult` (or propagates, per the caller's chosen method).
    """

    name: str

    def run(self, context: RunContext, /, *args: Any, **kwargs: Any) -> StageResult:
        ...


class CallableStage:
    """Delegating adapter: wraps an existing callable as a `WorkflowStage`.

    Does not require the wrapped callable to change signature or behavior.
    Each `run`/`run_capturing` call gets a fresh timing boundary and invokes
    the wrapped callable exactly once.

    Exception policy (mirrors `WorkflowRunner`'s doctrine):

    * `run()` — propagation is the default. Any exception raised by the
      wrapped callable propagates unchanged after a `stage.failed` event is
      emitted.
    * `run_capturing()` — explicit opt-in. Converts only `Exception`
      subclasses into a `FAILED` `StageResult`. `SystemExit`,
      `KeyboardInterrupt`, and any other non-`Exception` `BaseException`
      are never captured — they still propagate unchanged, after the same
      `stage.failed` event is emitted (every started stage gets exactly one
      terminal event, regardless of exception type).

    `parent_span_id` is only ever the value explicitly passed to the
    constructor. It is never inferred from `context.trace_reference`.

    A caller-supplied `sink` is used for lifecycle emission but is never
    closed by this class — sink ownership stays with the caller.
    """

    def __init__(
        self,
        name: str,
        func: Callable[..., Any],
        *,
        sink: EventSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
        span_id_factory: Callable[[], str] = new_span_id,
        parent_span_id: str | None = None,
    ) -> None:
        self.name = name
        self._func = func
        self._sink = sink
        self._clock = clock
        self._span_id_factory = span_id_factory
        self._parent_span_id = parent_span_id

    def run(self, context: RunContext, /, *args: Any, **kwargs: Any) -> StageResult:
        """Execute the wrapped callable, propagating any exception unchanged."""

        return self._execute(context, capture=False, args=args, kwargs=kwargs)

    def run_capturing(self, context: RunContext, /, *args: Any, **kwargs: Any) -> StageResult:
        """Execute the wrapped callable, converting `Exception` into a FAILED result."""

        return self._execute(context, capture=True, args=args, kwargs=kwargs)

    def _emit(self, event_type: EventType, run_id: str, **fields: Any) -> None:
        """Emit one lifecycle event. Never raises — a sink failure is
        swallowed here so it can never replace the callable's own
        exception (see `_execute`'s except block for the precedence
        rule this protects)."""

        if self._sink is None:
            return
        try:
            self._sink.emit(make_event(event_type, run_id, **fields))
        except Exception:  # noqa: BLE001 - deliberate: local sink failure must not
            # mask the real stage exception. Nothing else to do locally; the
            # canonical sink already logs/records failures at its own layer.
            pass

    def _execute(
        self,
        context: RunContext,
        *,
        capture: bool,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> StageResult:
        span_id = self._span_id_factory()
        previous_stage = context.current_stage
        context.current_stage = self.name
        started_at = self._clock()

        self._emit(
            EventType.STAGE_STARTED,
            context.run_id,
            timestamp=started_at.isoformat(),
            span_id=span_id,
            parent_span_id=self._parent_span_id,
            stage=self.name,
        )

        try:
            output = self._func(*args, **kwargs)
        except BaseException as exc:
            finished_at = self._clock()
            self._emit(
                EventType.STAGE_FAILED,
                context.run_id,
                timestamp=finished_at.isoformat(),
                span_id=span_id,
                parent_span_id=self._parent_span_id,
                stage=self.name,
                status=EventStatus.ERROR,
                duration_ms=(finished_at - started_at).total_seconds() * 1000.0,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            context.current_stage = previous_stage
            if capture and isinstance(exc, Exception):
                failure = StageFailure(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    stage=self.name,
                    propagated=False,
                )
                return StageResult(
                    stage_name=self.name,
                    status=StageStatus.FAILED,
                    started_at=started_at,
                    finished_at=finished_at,
                    failure=failure,
                    span_id=span_id,
                    parent_span_id=self._parent_span_id,
                )
            raise
        else:
            finished_at = self._clock()
            self._emit(
                EventType.STAGE_COMPLETED,
                context.run_id,
                timestamp=finished_at.isoformat(),
                span_id=span_id,
                parent_span_id=self._parent_span_id,
                stage=self.name,
                status=EventStatus.OK,
                duration_ms=(finished_at - started_at).total_seconds() * 1000.0,
            )
            context.current_stage = previous_stage
            return StageResult(
                stage_name=self.name,
                status=StageStatus.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                output=output,
                span_id=span_id,
                parent_span_id=self._parent_span_id,
            )
