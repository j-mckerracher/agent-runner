"""Prompt 8 — WorkflowRunner Shell.

Explicit, typed boundary around the existing `run.py::main` orchestration:

    RunSpec -> WorkflowRunner -> run.py::main -> WorkflowResult

This package only imports stdlib and `telemetry` at import time — no
`run`, `server`, or `opik` import happens until a `WorkflowRunner` with
the default adapter is actually invoked. See `docs/workflow-runner.md`.

Prompt 9 adds `WorkflowStage`/`StageResult`/`CallableStage` — explicit
per-stage execution contracts, wrapping existing callables rather than
rewriting them. See `docs/workflow-stages.md`.

Prompt 22 adds stage-artifact declarations/registry/validation
(`stage_artifacts`) and a telemetry-emitting lifecycle boundary
(`artifact_lifecycle`). See `docs/stage-artifact-contracts.md`.
"""

from .artifact_lifecycle import emit_stage_artifact_lifecycle
from .models import FailureDetail, RunContext, RunSpec, RunStatus, WorkflowResult
from .runner import LegacyWorkflowCallable, WorkflowRunner
from .stage_artifacts import (
    STAGE_ARTIFACT_REGISTRY,
    ArtifactDirection,
    Cardinality,
    ReferenceOutcome,
    SlotOutcome,
    StageArtifactDeclaration,
    StageArtifactRegistryError,
    StageArtifactSpec,
    StageArtifactValidationResult,
    UnknownStageError,
    get_stage_declaration,
    validate_stage_artifacts,
)
from .stages import CallableStage, StageFailure, StageResult, StageStatus, WorkflowStage

__all__ = [
    "RunSpec",
    "RunContext",
    "WorkflowResult",
    "WorkflowRunner",
    "RunStatus",
    "FailureDetail",
    "LegacyWorkflowCallable",
    "StageStatus",
    "StageFailure",
    "StageResult",
    "WorkflowStage",
    "CallableStage",
    # Prompt 22 — stage artifact contracts + lifecycle
    "ArtifactDirection",
    "Cardinality",
    "StageArtifactSpec",
    "StageArtifactDeclaration",
    "ReferenceOutcome",
    "SlotOutcome",
    "StageArtifactValidationResult",
    "STAGE_ARTIFACT_REGISTRY",
    "get_stage_declaration",
    "validate_stage_artifacts",
    "UnknownStageError",
    "StageArtifactRegistryError",
    "emit_stage_artifact_lifecycle",
]
