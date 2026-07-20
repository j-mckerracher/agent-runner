"""Prompt 19 — Structured validation primitives for artifact payload contracts.

Reusable, stdlib-only building blocks shared by the planning-artifact payload
contracts in `artifacts.payloads` (and, later, the P20 implementation-report /
QA-report contracts). Nothing here reads the filesystem, spawns a subprocess,
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

WARNING_CODES = frozenset(
    {
        WARNING_LEGACY_AC_LIST,
        WARNING_LEGACY_EXECUTION_SCHEDULE,
        WARNING_LEGACY_BATCH_KEY,
        WARNING_LEGACY_TASK_ID,
        WARNING_LEGACY_ACCEPTANCE_CRITERIA_MAPPED,
        WARNING_LEGACY_ESTIMATED_COMPLEXITY,
        WARNING_LEGACY_PARTIAL_UOW_SPEC,
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
