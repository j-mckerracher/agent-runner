"""Agent Workbench artifact contracts (public surface).

A stdlib-only leaf package: importing `artifacts` loads nothing from `core`,
`workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, PyYAML, or any
vendor SDK. It re-exports:

- Prompt 18 — the `ArtifactRef` reference contract (`artifacts.models`).
- Prompt 19 — the planning-artifact payload contracts and read-only loaders
  (`artifacts.payloads`) plus their structured validation primitives
  (`artifacts.validation`). YAML parsing is imported lazily inside the loaders,
  so `import artifacts` stays parser-free.
"""

from __future__ import annotations

from artifacts.models import (
    ARTIFACT_REF_SCHEMA_VERSION,
    SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS,
    ArtifactMetadataSerializationError,
    ArtifactRef,
    ArtifactRefValidationError,
    ArtifactValidationStatus,
)
from artifacts.payloads import (
    PLANNING_ARTIFACTS,
    AcceptanceCriterion,
    AssignmentArtifact,
    BatchEntry,
    PlanningArtifact,
    StoryArtifact,
    TaskEntry,
    TaskPlanArtifact,
    UowEntry,
    UowSpecArtifact,
)
from artifacts.validation import (
    ArtifactLoadError,
    ArtifactValidationError,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)

__all__ = [
    "ARTIFACT_REF_SCHEMA_VERSION",
    "SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS",
    "AcceptanceCriterion",
    "ArtifactLoadError",
    "ArtifactMetadataSerializationError",
    "ArtifactRef",
    "ArtifactRefValidationError",
    "ArtifactValidationError",
    "ArtifactValidationStatus",
    "AssignmentArtifact",
    "BatchEntry",
    "PLANNING_ARTIFACTS",
    "PlanningArtifact",
    "StoryArtifact",
    "TaskEntry",
    "TaskPlanArtifact",
    "UowEntry",
    "UowSpecArtifact",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
]
