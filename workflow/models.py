"""Prompt 8 — WorkflowRunner Shell: contract models.

Plain-stdlib dataclasses describing a workflow run's requested input
(`RunSpec`), per-run mutable state (`RunContext`), and structured output
(`WorkflowResult`). These models are a thin typed boundary around the
existing `run.py::main` orchestration — they do not change, extract, or
redesign any workflow behavior (see `docs/workflow-runner.md`).

No field here is invented beyond what `run.py::main` already accepts.
Validation mirrors `core.workflow_inputs.resolve_workflow_input` exactly:
only mutual exclusivity of the three story sources is enforced. Absence
of all three is *not* an error at this layer — the legacy seam silently
defaults to a synthetic fixture, and this shell must not diverge from
that behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from telemetry import new_run_id


class RunStatus(str, Enum):
    """Terminal status of a workflow run.

    Values mirror `run.py`'s own `STATUS_SUCCEEDED`/`STATUS_FAILED`
    strings (and `core.workflow_constants`) rather than telemetry event
    names — this is workflow status vocabulary, not an event type.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    return value if isinstance(value, Path) else Path(value)


@dataclass(frozen=True)
class RunSpec:
    """Immutable description of a single requested workflow run.

    Fields map directly to `run.py::main`'s parameters. Nothing here is
    speculative — there is no `output_dir`, `options`, `metadata`, or
    `event_sink` field, because the current seam has no wired use for
    them.
    """

    repo_path: Path | None = None
    change_id: str | None = None
    ado_url: str | None = None
    story_path: Path | None = None
    manual_story_path: Path | None = None
    runner: str = "claude"
    model: str | None = None
    agent_llm_overrides: Mapping[str, Mapping[str, str | None]] | None = None
    extra_context: str | None = None
    skip_lessons_optimizer: bool = True
    skip_materialize: bool = True
    calibration_fast_mode: bool = False
    headless: bool = False
    log_level: str = "warning"

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_path", _coerce_path(self.repo_path))
        object.__setattr__(self, "story_path", _coerce_path(self.story_path))
        object.__setattr__(self, "manual_story_path", _coerce_path(self.manual_story_path))

        provided = [
            name
            for name, value in (
                ("ado_url", self.ado_url),
                ("story_path", self.story_path),
                ("manual_story_path", self.manual_story_path),
            )
            if value
        ]
        if len(provided) > 1:
            # Mirrors core.workflow_inputs.resolve_workflow_input exactly.
            raise ValueError(
                "Provide only one of ado_url, story_path, or manual_story_path."
            )
        # Intentionally no "at least one required" check: the legacy
        # seam defaults to a synthetic fixture when none are given.


@dataclass
class RunContext:
    """Mutable, per-run state created fresh for each `WorkflowRunner.run` call.

    This is not a global and is never shared between runs. It carries
    only state the shell itself owns; it does not duplicate any path
    resolution that `run.py::main` performs internally.
    """

    run_id: str
    spec: RunSpec
    started_at: datetime
    current_stage: str | None = None
    trace_reference: Any | None = None
    artifact_references: tuple[Any, ...] = ()
    runtime_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_spec(
        cls,
        spec: RunSpec,
        *,
        run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> "RunContext":
        return cls(
            run_id=run_id or new_run_id(),
            spec=spec,
            started_at=started_at or _utcnow(),
        )


@dataclass
class FailureDetail:
    """Structured description of a run that ended in an exception."""

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
class WorkflowResult:
    """Structured output of a single `WorkflowRunner.run`/`run_capturing` call."""

    run_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    final_output: Any | None = None
    failure: FailureDetail | None = None
    trace_reference: Any | None = None
    artifact_references: tuple[Any, ...] = ()
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        delta = self.finished_at - self.started_at
        return delta.total_seconds() * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "final_output": self.final_output,
            "failure": self.failure.to_dict() if self.failure else None,
            "trace_reference": self.trace_reference,
            "artifact_references": list(self.artifact_references),
            "runtime_metadata": dict(self.runtime_metadata),
        }
