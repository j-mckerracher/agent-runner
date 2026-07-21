"""Agent Workbench artifact contracts (public surface).

A stdlib-only leaf package: importing `artifacts` loads nothing from `core`,
`workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, PyYAML, or any
vendor SDK. It re-exports:

- Prompt 18 — the `ArtifactRef` reference contract (`artifacts.models`).
- Prompt 19 — the planning-artifact payload contracts and read-only loaders
  (`artifacts.payloads`) plus their structured validation primitives
  (`artifacts.validation`). YAML parsing is imported lazily inside the loaders,
  so `import artifacts` stays parser-free.
- Prompt 20 — the implementation-report and QA-report payload contracts and
  loaders, extending the same `artifacts.payloads` module and conventions.
- Prompt 21 — `FinalDiffArtifact` evidence leaf (`artifacts.evidence`).
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
    REPORT_ARTIFACTS,
    AcceptanceCriterion,
    AssignmentArtifact,
    BatchEntry,
    CommandEntry,
    DefinitionOfDoneItem,
    EvidenceManifest,
    FileChangeEntry,
    ImplementationReportArtifact,
    ImplementationReportPayload,
    IssueEntry,
    PlanningArtifact,
    QAAcValidation,
    QAReportArtifact,
    QAReportPayload,
    RegressionRiskAssessment,
    ReleaseNotes,
    RiskEntry,
    StoryArtifact,
    TaskEntry,
    TaskPlanArtifact,
    TestEntry,
    UowEntry,
    UowSpecArtifact,
    load_implementation_report,
    load_qa_report,
)
from artifacts.validation import (
    ArtifactLoadError,
    ArtifactValidationError,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from artifacts.evidence import FinalDiffArtifact

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
    "CommandEntry",
    "DefinitionOfDoneItem",
    "EvidenceManifest",
    "FileChangeEntry",
    "FinalDiffArtifact",
    "ImplementationReportArtifact",
    "ImplementationReportPayload",
    "IssueEntry",
    "load_implementation_report",
    "load_qa_report",
    "PLANNING_ARTIFACTS",
    "PlanningArtifact",
    "QAAcValidation",
    "QAReportArtifact",
    "QAReportPayload",
    "REPORT_ARTIFACTS",
    "RegressionRiskAssessment",
    "ReleaseNotes",
    "RiskEntry",
    "StoryArtifact",
    "TaskEntry",
    "TaskPlanArtifact",
    "TestEntry",
    "UowEntry",
    "UowSpecArtifact",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
]
