"""Prompt 19 — Structured validation primitives for artifact payload contracts.

Reusable, stdlib-only building blocks shared by the planning-artifact payload
contracts (P19) and the implementation-report / QA-report contracts (P20) in
`artifacts.payloads`. Nothing here reads the filesystem, spawns a subprocess,
touches the network, or imports `core`/`workflow`/`eval`/`server`/any vendor
SDK — it stays a leaf of the `artifacts` package.

The model is deliberately small:

- A *validation* produces a `ValidationResult` — an ordered, immutable tuple of
  `ValidationIssue` objects, each tagged with a stable machine-readable `code`
  and a `ValidationSeverity` (`ERROR` or `WARNING`).
- ERROR issues mean the payload cannot be trusted as the modeled contract;
  WARNING issues mean an accepted legacy shape was normalized in-memory and the
  caller may want to know (they never fail loading).
- `ArtifactValidationError` is raised only when a result carries ERROR issues;
  it exposes the full `result` and its ERROR `.errors` so callers see every
  problem at once (mirroring `artifacts.models.ArtifactRefValidationError`).
- `ArtifactLoadError` is a *transport* failure — a missing file, an unreadable
  path, malformed YAML/JSON, or a missing PyYAML dependency — distinct from a
  structurally-invalid-but-readable payload.

`ValidationResult` is returned, not raised, so a caller can inspect warnings
(accepted legacy shapes) even on a successful load. Producers of these results
must not substitute Python `warnings` / logging for the structured result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValidationSeverity(str, Enum):
    """Severity of a single validation issue.

    `ERROR` invalidates the payload; `WARNING` records an accepted legacy shape
    that was normalized in-memory (loading still succeeds).
    """

    ERROR = "error"
    WARNING = "warning"


# --------------------------------------------------------------------------
# Stable, machine-readable code catalogs.
#
# These strings are part of the contract: tooling may branch on them, so they
# are frozen constants rather than ad-hoc literals scattered through the
# payload parsers.
# --------------------------------------------------------------------------

# ERROR codes — a payload carrying any of these is not the modeled contract.
ERROR_MISSING_FIELD = "missing_field"
ERROR_WRONG_TYPE = "wrong_type"
ERROR_EMPTY_COLLECTION = "empty_collection"
ERROR_NOT_A_MAPPING = "not_a_mapping"
ERROR_INVALID_VALUE = "invalid_value"

ERROR_CODES = frozenset(
    {
        ERROR_MISSING_FIELD,
        ERROR_WRONG_TYPE,
        ERROR_EMPTY_COLLECTION,
        ERROR_NOT_A_MAPPING,
        ERROR_INVALID_VALUE,
    }
)

# WARNING codes — an accepted legacy shape was normalized in-memory.
WARNING_LEGACY_AC_LIST = "legacy_ac_list"
WARNING_LEGACY_EXECUTION_SCHEDULE = "legacy_execution_schedule"
WARNING_LEGACY_BATCH_KEY = "legacy_batch_key"
WARNING_LEGACY_TASK_ID = "legacy_task_id"
WARNING_LEGACY_ACCEPTANCE_CRITERIA_MAPPED = "legacy_acceptance_criteria_mapped"
WARNING_LEGACY_ESTIMATED_COMPLEXITY = "legacy_estimated_complexity"
WARNING_LEGACY_PARTIAL_UOW_SPEC = "legacy_partial_uow_spec"
WARNING_LEGACY_DEFINITION_OF_DONE = "legacy_definition_of_done"

# Implementation-report legacy shapes — each accepted historical variant gets
# its own stable code; none of these are a generic catch-all.
WARNING_LEGACY_IMPL_SUMMARY = "legacy_impl_summary"
WARNING_LEGACY_IMPL_STATUS_COMPLETED = "legacy_impl_status_completed"
WARNING_LEGACY_FILES_CHANGED = "legacy_files_changed"
WARNING_LEGACY_FILE_PATH_STRING = "legacy_file_path_string"
WARNING_LEGACY_IMPL_STORY_ID = "legacy_impl_story_id"

# QA-report legacy shapes.
WARNING_LEGACY_QA_CHANGE_ID = "legacy_qa_change_id"
WARNING_LEGACY_QA_OVERALL_STATUS = "legacy_qa_overall_status"
WARNING_LEGACY_QA_AC_LIST = "legacy_qa_ac_list"
WARNING_LEGACY_QA_EVIDENCE_STRING = "legacy_qa_evidence_string"
WARNING_LEGACY_QA_EVIDENCE_LIST = "legacy_qa_evidence_list"
WARNING_LEGACY_QA_CONDITIONAL_PASS = "legacy_qa_conditional_pass"
WARNING_LEGACY_QA_REGRESSION_RISK = "legacy_qa_regression_risk"
WARNING_LEGACY_QA_RELEASE_NOTES_STRING = "legacy_qa_release_notes_string"
WARNING_LEGACY_QA_RELEASE_NOTES_LIST = "legacy_qa_release_notes_list"

WARNING_CODES = frozenset(
    {
        WARNING_LEGACY_AC_LIST,
        WARNING_LEGACY_EXECUTION_SCHEDULE,
        WARNING_LEGACY_BATCH_KEY,
        WARNING_LEGACY_TASK_ID,
        WARNING_LEGACY_ACCEPTANCE_CRITERIA_MAPPED,
        WARNING_LEGACY_ESTIMATED_COMPLEXITY,
        WARNING_LEGACY_PARTIAL_UOW_SPEC,
        WARNING_LEGACY_DEFINITION_OF_DONE,
        WARNING_LEGACY_IMPL_SUMMARY,
        WARNING_LEGACY_IMPL_STATUS_COMPLETED,
        WARNING_LEGACY_FILES_CHANGED,
        WARNING_LEGACY_FILE_PATH_STRING,
        WARNING_LEGACY_IMPL_STORY_ID,
        WARNING_LEGACY_QA_CHANGE_ID,
        WARNING_LEGACY_QA_OVERALL_STATUS,
        WARNING_LEGACY_QA_AC_LIST,
        WARNING_LEGACY_QA_EVIDENCE_STRING,
        WARNING_LEGACY_QA_EVIDENCE_LIST,
        WARNING_LEGACY_QA_CONDITIONAL_PASS,
        WARNING_LEGACY_QA_REGRESSION_RISK,
        WARNING_LEGACY_QA_RELEASE_NOTES_STRING,
        WARNING_LEGACY_QA_RELEASE_NOTES_LIST,
    }
)


@dataclass(frozen=True, kw_only=True)
class ValidationIssue:
    """One structured problem found while validating a payload.

    Frozen and keyword-only. `code` is a stable catalog string (see
    `ERROR_CODES` / `WARNING_CODES`); `location` is an optional dotted/indexed
    path into the payload (e.g. ``tasks[2].id``) for actionable diagnostics.
    """

    code: str
    message: str
    severity: ValidationSeverity
    location: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict; `location` omitted when absent."""
        result: dict = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.location is not None:
            result["location"] = self.location
        return result


@dataclass(frozen=True)
class ValidationResult:
    """Immutable, ordered collection of `ValidationIssue` objects.

    `ok` is true when there are no ERROR issues (WARNING-only results are still
    `ok`). Never raises on construction; `raise_for_errors` is the explicit
    opt-in that converts an error-carrying result into an exception.
    """

    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        # Defensively coerce any iterable (e.g. a caller-supplied list) into a
        # concrete tuple so a later mutation of the caller's original object
        # cannot be observed through this frozen result.
        if not isinstance(self.issues, tuple):
            object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is ValidationSeverity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}

    def raise_for_errors(self, where: str) -> "ValidationResult":
        """Raise `ArtifactValidationError` if any ERROR issues exist; else self."""
        if self.errors:
            raise ArtifactValidationError(self, where)
        return self


class ArtifactLoadError(Exception):
    """Raised when an artifact cannot be read or parsed from its source.

    Covers a missing file, an unreadable path, malformed YAML/JSON, or a
    missing PyYAML dependency — transport-level failures distinct from a
    readable-but-structurally-invalid payload (which yields
    `ArtifactValidationError`). The message always names the source path.
    """


class ArtifactValidationError(ValueError):
    """Raised when a payload fails structural validation.

    Carries the full `result` and its ERROR `.errors` tuple so callers can see
    every problem at once. The message renders each error as ``code: message``,
    falling back to ``invalid <where>`` when there are no error issues.
    """

    def __init__(self, result: ValidationResult, where: str | None = None):
        self.result = result
        self.errors: tuple[ValidationIssue, ...] = result.errors
        self.where = where
        if self.errors:
            message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.errors)
        else:
            message = f"invalid {where or 'artifact'}"
        super().__init__(message)
