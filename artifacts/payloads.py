"""Prompt 19/20 — Versioned payload contracts for the planning and report artifacts.

Typed, immutable, read-only *payload* models for the four planning-stage
artifacts, layered on top of the P18 `ArtifactRef` reference contract:

- `StoryArtifact`          — `{change_id}/intake/story.yaml`
- `TaskPlanArtifact`       — `{change_id}/planning/tasks.yaml`
- `AssignmentArtifact`     — `{change_id}/planning/assignments.json`
- `UowSpecArtifact`        — `{change_id}/execution/{uow_id}/uow_spec.yaml`

and, added in Prompt 20, the two workflow report artifacts:

- `ImplementationReportPayload` — `{change_id}/execution/{uow_id}/impl_report.yaml`
- `QAReportPayload`             — `{change_id}/qa/qa_report.yaml`

Design boundaries (kept deliberately narrow):

- `artifacts` stays a stdlib-only leaf: importing this module pulls in nothing
  from `core`/`workflow`/`eval`/`server`/a vendor SDK, and — crucially — does
  **not** import PyYAML. YAML is imported lazily, at call time, only inside
  `load*()` when a caller actually reads a YAML file. `from_mapping*()` and all
  validation are pure and parser-free.
- The four planning-stage classes are *core-field projections*, not lossless
  mirrors of the source file: unmodeled top-level keys (`critical_path`,
  `ac_coverage_matrix`, `notes`, `metacognitive_context`, …) are ignored.
  `to_dict()` is therefore a round trip of the *typed model*, not of the source
  document.
- The two report classes instead *preserve* unmodeled top-level keys as
  read-only extension data (see `_extract_extension`), because their generated
  agent reports carry many evolving sections that must survive a load/serialize
  round trip untouched.
- Loading is strictly read-only. Normalization of accepted legacy shapes happens
  on a defensive deep copy and is reported as WARNING issues in a
  `ValidationResult`; the caller's mapping and the on-disk file are never
  mutated.
- Structural problems are ERROR issues aggregated into `ArtifactValidationError`;
  transport failures (missing file, bad YAML/JSON, missing PyYAML) raise
  `ArtifactLoadError`.

This module defines contracts and loaders only. No producer or consumer adopts
them yet; eval artifacts (P21) remain out of scope.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from artifacts.models import ArtifactRef, ArtifactValidationStatus
from artifacts.validation import (
    ERROR_EMPTY_COLLECTION,
    ERROR_INVALID_VALUE,
    ERROR_MISSING_FIELD,
    ERROR_NOT_A_MAPPING,
    ERROR_WRONG_TYPE,
    WARNING_LEGACY_AC_LIST,
    WARNING_LEGACY_ACCEPTANCE_CRITERIA_MAPPED,
    WARNING_LEGACY_BATCH_KEY,
    WARNING_LEGACY_DEFINITION_OF_DONE,
    WARNING_LEGACY_ESTIMATED_COMPLEXITY,
    WARNING_LEGACY_EXECUTION_SCHEDULE,
    WARNING_LEGACY_PARTIAL_UOW_SPEC,
    WARNING_LEGACY_TASK_ID,
    ArtifactLoadError,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)

__all__ = [
    "AcceptanceCriterion",
    "AssignmentArtifact",
    "BatchEntry",
    "DefinitionOfDoneItem",
    "ImplementationReportPayload",
    "load_implementation_report",
    "load_qa_report",
    "PLANNING_ARTIFACTS",
    "PlanningArtifact",
    "QAAcValidation",
    "QAReportPayload",
    "StoryArtifact",
    "TaskEntry",
    "TaskPlanArtifact",
    "UowEntry",
    "UowSpecArtifact",
]

# Logical content formats (declarative; no parser is bound here).
_FORMAT_YAML = "yaml"
_FORMAT_JSON = "json"

# Enum constraints mirrored from `agent-script-source/validate-artifact-schema.py`.
_VALID_TASK_PRIORITIES = ("high", "medium", "low")
_VALID_TASK_COMPLEXITIES = ("simple", "moderate", "complex")

# Enum constraints for the P20 report contracts, mirrored from
# `agent-definition-source/software-engineer/v2/prompt.md` and
# `agent-definition-source/qa/v2/prompt.md`.
_VALID_IMPL_STATUSES = ("complete", "partial", "blocked")
_VALID_QA_STATUSES = ("pass", "fail", "blocked")
_VALID_AC_VALIDATION_STATUSES = ("pass", "fail", "partial")
_VALID_FINAL_RECOMMENDATIONS = ("approve", "reject", "approve_with_conditions")

# The only currently supported document-embedded report schema_version. A
# missing schema_version in a legacy report defaults to this value, and it is
# the canonical value emitted on serialization (mirrors the frozenset/str-type
# validation pattern already established by
# `artifacts.models.SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS`).
_REPORT_SCHEMA_VERSION = "1"
_SUPPORTED_REPORT_SCHEMA_VERSIONS = frozenset({_REPORT_SCHEMA_VERSION})


# ---------------------------------------------------------------------------
# Issue + field helpers (pure; append to a caller-owned issue list).
# ---------------------------------------------------------------------------


def _err(code: str, message: str, location: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, message=message, severity=ValidationSeverity.ERROR, location=location
    )


def _warn(code: str, message: str, location: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, message=message, severity=ValidationSeverity.WARNING, location=location
    )


def _require_mapping(
    data: Any, where: str, issues: list[ValidationIssue]
) -> dict[str, Any] | None:
    """Return a defensive deep copy of a mapping payload, or record an error.

    The copy guarantees that legacy-shape normalization never mutates the
    caller's object.
    """
    if not isinstance(data, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(data).__name__}", where)
        )
        return None
    return copy.deepcopy(dict(data))


def _get_str(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    issues: list[ValidationIssue],
    *,
    required: bool,
    default: str = "",
    allow_empty: bool = False,
) -> str:
    raw = mapping.get(key)
    if raw is None:
        if required:
            issues.append(_err(ERROR_MISSING_FIELD, f"missing required field '{key}'", where))
        return default
    if not isinstance(raw, str):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be a string, got {type(raw).__name__}", where)
        )
        return default
    if not raw.strip() and not allow_empty:
        if required:
            issues.append(_err(ERROR_MISSING_FIELD, f"field '{key}' must be non-empty", where))
        return default if required else raw
    return raw


def _get_opt_str(
    mapping: Mapping[str, Any], key: str, where: str, issues: list[ValidationIssue]
) -> str | None:
    raw = mapping.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be a string, got {type(raw).__name__}", where)
        )
        return None
    return raw


def _get_str_enum(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    issues: list[ValidationIssue],
    *,
    valid_values: tuple[str, ...],
    required: bool = False,
) -> str | None:
    raw = mapping.get(key)
    if raw is None:
        if required:
            issues.append(_err(ERROR_MISSING_FIELD, f"missing required field '{key}'", where))
        return None
    if not isinstance(raw, str):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be a string, got {type(raw).__name__}", where)
        )
        return None
    if raw not in valid_values:
        issues.append(
            _err(
                ERROR_INVALID_VALUE,
                f"field '{key}' must be one of {', '.join(valid_values)}, got {raw!r}",
                where,
            )
        )
        return None
    return raw


def _get_opt_str_enum(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    issues: list[ValidationIssue],
    *,
    valid_values: tuple[str, ...],
) -> str | None:
    return _get_str_enum(mapping, key, where, issues, valid_values=valid_values, required=False)


def _get_bool(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    issues: list[ValidationIssue],
    *,
    required: bool,
) -> bool | None:
    raw = mapping.get(key)
    if raw is None:
        if required:
            issues.append(_err(ERROR_MISSING_FIELD, f"missing required field '{key}'", where))
        return None
    # Explicit isinstance(..., bool) rejects both integer truthiness (0/1) and
    # string truthiness ("true"/"false") — only an actual bool is accepted.
    if not isinstance(raw, bool):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be a boolean, got {type(raw).__name__}", where)
        )
        return None
    return raw


def _get_schema_version(
    mapping: Mapping[str, Any], where: str, issues: list[ValidationIssue]
) -> str:
    """Parse a document-embedded `schema_version`, defaulting/validating per P20.

    A missing `schema_version` defaults to the supported legacy version. A
    boolean or any non-string value is rejected as the wrong type (bool is an
    int subclass, so it is rejected before any value comparison). A string
    outside `_SUPPORTED_REPORT_SCHEMA_VERSIONS` — malformed or an unsupported
    future version — is rejected with an actionable message naming the
    supported set. The canonical version is emitted on serialization
    regardless of what a caller passes here, since only one version currently
    exists.
    """
    raw = mapping.get("schema_version")
    if raw is None:
        return _REPORT_SCHEMA_VERSION
    if not isinstance(raw, str):
        issues.append(
            _err(
                ERROR_WRONG_TYPE,
                f"field 'schema_version' must be a string, got {type(raw).__name__}",
                where,
            )
        )
        return _REPORT_SCHEMA_VERSION
    if raw not in _SUPPORTED_REPORT_SCHEMA_VERSIONS:
        issues.append(
            _err(
                ERROR_INVALID_VALUE,
                f"unsupported schema_version {raw!r}; supported versions: "
                f"{sorted(_SUPPORTED_REPORT_SCHEMA_VERSIONS)}",
                where,
            )
        )
        return _REPORT_SCHEMA_VERSION
    return raw


def _get_opt_int(
    mapping: Mapping[str, Any], key: str, where: str, issues: list[ValidationIssue]
) -> int | None:
    raw = mapping.get(key)
    if raw is None:
        return None
    # bool is an int subclass; reject it explicitly so True/False is not a batch index.
    if isinstance(raw, bool) or not isinstance(raw, int):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be an integer, got {type(raw).__name__}", where)
        )
        return None
    return raw


def _get_str_tuple(
    mapping: Mapping[str, Any],
    key: str,
    where: str,
    issues: list[ValidationIssue],
    *,
    required: bool = False,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    raw = mapping.get(key)
    if raw is None:
        if required:
            issues.append(_err(ERROR_MISSING_FIELD, f"missing required field '{key}'", where))
        return ()
    if not isinstance(raw, list):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field '{key}' must be a list, got {type(raw).__name__}", where)
        )
        return ()
    items: list[str] = []
    for idx, value in enumerate(raw):
        if not isinstance(value, str):
            issues.append(
                _err(
                    ERROR_WRONG_TYPE,
                    f"item must be a string, got {type(value).__name__}",
                    f"{where}.{key}[{idx}]",
                )
            )
            continue
        items.append(value)
    if not items and not allow_empty:
        issues.append(_err(ERROR_EMPTY_COLLECTION, f"field '{key}' must be non-empty", where))
    return tuple(items)


# ---------------------------------------------------------------------------
# Extension-data preservation (P20 report contracts only).
#
# Unlike the four planning-stage projections above, the report contracts must
# not silently drop unmodeled top-level keys — generated agent reports carry
# many evolving sections that must survive a load/serialize round trip. Parsed
# YAML/JSON data is already restricted to safe built-in types (mapping, list,
# str, int, float, bool, None), so a simple recursive freeze/thaw is enough;
# this deliberately does not reuse `artifacts.models`' cycle-safe
# canonicalization graph, which exists to handle arbitrary caller-supplied
# `ArtifactRef.metadata`, not parsed document data.
# ---------------------------------------------------------------------------


def _freeze_extension_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_extension_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_extension_value(item) for item in value)
    return value


def _thaw_extension_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_extension_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_extension_value(item) for item in value]
    return value


def _extract_extension(mapping: Mapping[str, Any], recognized_keys: frozenset[str]) -> Mapping[str, Any]:
    """Return the unrecognized top-level keys of `mapping` as a frozen mapping.

    `mapping` is already a defensive deep copy (from `_require_mapping`), so no
    further copying is needed before freezing.
    """
    extension = {key: value for key, value in mapping.items() if key not in recognized_keys}
    return _freeze_extension_value(extension)


# ---------------------------------------------------------------------------
# Lazy YAML loader (call-time only; keeps `import artifacts` PyYAML-free).
# ---------------------------------------------------------------------------


def _import_yaml():
    """Import PyYAML lazily. Isolated so tests can simulate its absence."""
    import yaml  # noqa: PLC0415 — deliberate call-time import for isolation

    return yaml


def _load_yaml_mapping(text: str, path: Path) -> Any:
    try:
        yaml = _import_yaml()
    except ModuleNotFoundError as exc:
        raise ArtifactLoadError(
            f"{path}: loading YAML artifacts requires PyYAML (pip install PyYAML)"
        ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ArtifactLoadError(f"{path}: invalid YAML: {exc}") from exc


# ---------------------------------------------------------------------------
# Nested payload entries.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AcceptanceCriterion:
    """One acceptance criterion: a stable id and its text."""

    ac_id: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"ac_id": self.ac_id, "text": self.text}


@dataclass(frozen=True, kw_only=True)
class TaskEntry:
    """One task inside a task plan."""

    id: str
    title: str
    ac_mapping: tuple[str, ...]
    description: str = ""
    dependencies: tuple[str, ...] = ()
    priority: str | None = None
    complexity: str | None = None
    definition_of_done: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "ac_mapping": list(self.ac_mapping),
        }
        if self.description:
            result["description"] = self.description
        if self.dependencies:
            result["dependencies"] = list(self.dependencies)
        if self.priority is not None:
            result["priority"] = self.priority
        if self.complexity is not None:
            result["complexity"] = self.complexity
        if self.definition_of_done:
            result["definition_of_done"] = list(self.definition_of_done)
        return result


@dataclass(frozen=True, kw_only=True)
class UowEntry:
    """One unit of work inside an assignment batch."""

    uow_id: str
    source_task_id: str
    title: str | None = None
    assigned_role: str | None = None
    priority_in_batch: int | None = None
    rationale: str | None = None
    dependencies: tuple[str, ...] = ()
    definition_of_done: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "uow_id": self.uow_id,
            "source_task_id": self.source_task_id,
        }
        if self.title is not None:
            result["title"] = self.title
        if self.assigned_role is not None:
            result["assigned_role"] = self.assigned_role
        if self.priority_in_batch is not None:
            result["priority_in_batch"] = self.priority_in_batch
        if self.rationale is not None:
            result["rationale"] = self.rationale
        if self.dependencies:
            result["dependencies"] = list(self.dependencies)
        if self.definition_of_done:
            result["definition_of_done"] = list(self.definition_of_done)
        return result


@dataclass(frozen=True, kw_only=True)
class BatchEntry:
    """One execution batch inside an assignment."""

    batch_id: int
    uows: tuple[UowEntry, ...]
    parallel_execution: bool = False
    batch_rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "batch_id": self.batch_id,
            "uows": [uow.to_dict() for uow in self.uows],
        }
        if self.parallel_execution:
            result["parallel_execution"] = True
        if self.batch_rationale is not None:
            result["batch_rationale"] = self.batch_rationale
        return result


@dataclass(frozen=True, kw_only=True)
class DefinitionOfDoneItem:
    """One `definition_of_done_status` entry inside an implementation report."""

    item: str
    met: bool
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"item": self.item, "met": self.met}
        if self.evidence is not None:
            result["evidence"] = self.evidence
        return result


@dataclass(frozen=True, kw_only=True)
class QAAcValidation:
    """One `acceptance_criteria_validation` entry inside a QA report."""

    ac_id: str
    status: str
    validation_method: str | None = None
    evidence_type: str | None = None
    evidence_reference: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.validation_method is not None:
            result["validation_method"] = self.validation_method
        evidence: dict[str, Any] = {}
        if self.evidence_type is not None:
            evidence["type"] = self.evidence_type
        if self.evidence_reference is not None:
            evidence["reference"] = self.evidence_reference
        if evidence:
            result["evidence"] = evidence
        if self.notes is not None:
            result["notes"] = self.notes
        return result


# ---------------------------------------------------------------------------
# Base contract: shared metadata, validation wrappers, loaders, ref factory.
# ---------------------------------------------------------------------------


class PlanningArtifact:
    """Base for the four planning-artifact payload contracts.

    Subclasses set the `ClassVar` contract metadata and implement `_parse`,
    which is the single normalization/validation entry point. Everything else
    (public validation wrappers, read-only loaders, the `ArtifactRef` factory)
    is shared here.
    """

    # Contract metadata — ClassVar so the dataclass subclasses never treat these
    # as payload fields.
    ARTIFACT_TYPE: ClassVar[str]
    ARTIFACT_SCHEMA: ClassVar[str]
    ARTIFACT_SCHEMA_VERSION: ClassVar[str]
    PRODUCER_STAGE: ClassVar[str]
    CONSUMER_STAGES: ClassVar[tuple[str, ...]]
    PATH_SCOPE: ClassVar[str]
    RELATIVE_PATH_TEMPLATE: ClassVar[str]
    PATH_PARAMETERS: ClassVar[tuple[str, ...]]
    CONTENT_FORMAT: ClassVar[str]

    @classmethod
    def _parse(cls, data: Any) -> tuple[Any, ValidationResult]:
        """Normalize + validate `data`, returning (instance_or_None, result).

        Never raises: structural problems are ERROR issues, accepted legacy
        shapes are WARNING issues. The instance is `None` iff the result has
        errors.
        """
        raise NotImplementedError

    @classmethod
    def validate_payload(cls, data: Any) -> ValidationResult:
        """Validate a parsed payload without constructing; never raises."""
        _, result = cls._parse(data)
        return result

    @classmethod
    def from_mapping_with_validation(cls, data: Any) -> tuple[Any, ValidationResult]:
        """Build from a parsed mapping, returning (instance, result).

        Compatibility warnings are observable in `result`. Raises
        `ArtifactValidationError` only when the payload has ERROR issues.
        """
        instance, result = cls._parse(data)
        result.raise_for_errors(cls.__name__)
        return instance, result

    @classmethod
    def from_mapping(cls, data: Any) -> Any:
        """Convenience wrapper: build from a mapping, discarding warnings."""
        instance, _ = cls.from_mapping_with_validation(data)
        return instance

    @classmethod
    def load_with_validation(cls, path: str | Path) -> tuple[Any, ValidationResult]:
        """Read-only load from `path`, returning (instance, result).

        Reads the file text, parses it by `CONTENT_FORMAT` (stdlib `json`, or
        PyYAML imported lazily), then validates. Transport failures raise
        `ArtifactLoadError`; structural failures raise `ArtifactValidationError`.
        The source file is never opened for writing.
        """
        resolved = Path(path)
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactLoadError(f"{resolved}: cannot read artifact: {exc}") from exc
        if cls.CONTENT_FORMAT == _FORMAT_JSON:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ArtifactLoadError(f"{resolved}: invalid JSON: {exc}") from exc
        else:
            data = _load_yaml_mapping(text, resolved)
        return cls.from_mapping_with_validation(data)

    @classmethod
    def load(cls, path: str | Path) -> Any:
        """Convenience wrapper: read-only load, discarding warnings."""
        instance, _ = cls.load_with_validation(path)
        return instance

    def to_artifact_ref(
        self,
        *,
        path: str | Path | None = None,
        uri: str | None = None,
        validation_status: ArtifactValidationStatus | str | None = None,
        checksum_sha256: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Create an `ArtifactRef` from this contract and an explicit location.

        Populates canonical `artifact_type`, `producer_stage`,
        `consumer_stages`, `artifact_schema`, and `artifact_schema_version`. The
        template is not resolved, no file is read, no checksum is computed, and
        the caller must supply `path` and/or `uri` (`ArtifactRef` enforces this).
        `metadata` is defensively copied by `ArtifactRef`.
        """
        kwargs: dict[str, Any] = {
            "artifact_type": self.ARTIFACT_TYPE,
            "producer_stage": self.PRODUCER_STAGE,
            "consumer_stages": self.CONSUMER_STAGES,
            "artifact_schema": self.ARTIFACT_SCHEMA,
            "artifact_schema_version": self.ARTIFACT_SCHEMA_VERSION,
        }
        if path is not None:
            kwargs["path"] = path
        if uri is not None:
            kwargs["uri"] = uri
        if validation_status is not None:
            kwargs["validation_status"] = validation_status
        if checksum_sha256 is not None:
            kwargs["checksum_sha256"] = checksum_sha256
        # Omit metadata entirely when None so ArtifactRef's own default applies.
        if metadata is not None:
            kwargs["metadata"] = metadata
        return ArtifactRef(**kwargs)


# ---------------------------------------------------------------------------
# Story.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StoryArtifact(PlanningArtifact):
    """Normalized intake story (`{change_id}/intake/story.yaml`)."""

    ARTIFACT_TYPE: ClassVar[str] = "story"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.story"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "intake"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = (
        "task-generation",
        "task-assignment",
        "qa",
        "pr-review",
    )
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/intake/story.yaml"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id",)
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_YAML

    change_id: str
    title: str
    description: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]

    @classmethod
    def _parse(cls, data: Any) -> tuple["StoryArtifact | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        change_id = _get_str(mapping, "change_id", where, issues, required=True)
        title = _get_str(mapping, "title", where, issues, required=True)
        description = _get_str(mapping, "description", where, issues, required=True)
        acceptance_criteria = _normalize_acceptance_criteria(
            mapping.get("acceptance_criteria"), where, issues
        )

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return (
            cls(
                change_id=change_id,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
            ),
            result,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": {ac.ac_id: ac.text for ac in self.acceptance_criteria},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def _validate_ac_string(
    value: Any, what: str, location: str, issues: list[ValidationIssue]
) -> str | None:
    """Require `value` to already be a non-empty string; never coerce via `str()`."""
    if not isinstance(value, str):
        issues.append(_err(ERROR_WRONG_TYPE, f"{what} must be a string, got {type(value).__name__}", location))
        return None
    if not value.strip():
        issues.append(_err(ERROR_MISSING_FIELD, f"{what} must be non-empty", location))
        return None
    return value


def _normalize_acceptance_criteria(
    raw: Any, where: str, issues: list[ValidationIssue]
) -> tuple[AcceptanceCriterion, ...]:
    if raw is None:
        issues.append(_err(ERROR_MISSING_FIELD, "missing required field 'acceptance_criteria'", where))
        return ()
    if isinstance(raw, Mapping):
        criteria: list[AcceptanceCriterion] = []
        for key, value in raw.items():
            location = f"{where}.acceptance_criteria[{key!r}]"
            ac_id = _validate_ac_string(key, "acceptance_criteria key", location, issues)
            text = _validate_ac_string(value, "acceptance_criteria value", location, issues)
            if ac_id is not None and text is not None:
                criteria.append(AcceptanceCriterion(ac_id=ac_id, text=text))
        if not criteria:
            issues.append(
                _err(ERROR_EMPTY_COLLECTION, "field 'acceptance_criteria' must be non-empty", where)
            )
        return tuple(criteria)
    if isinstance(raw, list):
        issues.append(
            _warn(
                WARNING_LEGACY_AC_LIST,
                "acceptance_criteria given as a list; auto-numbered AC1..N",
                where,
            )
        )
        criteria = []
        for index, value in enumerate(raw, start=1):
            location = f"{where}.acceptance_criteria[{index}]"
            text = _validate_ac_string(value, "acceptance_criteria item", location, issues)
            if text is not None:
                criteria.append(AcceptanceCriterion(ac_id=f"AC{index}", text=text))
        if not criteria:
            issues.append(
                _err(ERROR_EMPTY_COLLECTION, "field 'acceptance_criteria' must be non-empty", where)
            )
        return tuple(criteria)
    issues.append(
        _err(
            ERROR_WRONG_TYPE,
            f"field 'acceptance_criteria' must be a mapping or list, got {type(raw).__name__}",
            where,
        )
    )
    return ()


# ---------------------------------------------------------------------------
# Task plan.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class TaskPlanArtifact(PlanningArtifact):
    """Task plan (`{change_id}/planning/tasks.yaml`)."""

    ARTIFACT_TYPE: ClassVar[str] = "task_plan"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.task-plan"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "task-generation"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("task-assignment", "qa", "pr-review")
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/planning/tasks.yaml"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id",)
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_YAML

    tasks: tuple[TaskEntry, ...]
    story_id: str | None = None

    @classmethod
    def _parse(cls, data: Any) -> tuple["TaskPlanArtifact | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        story_id = _get_opt_str(mapping, "story_id", where, issues)
        tasks: list[TaskEntry] = []
        raw_tasks = mapping.get("tasks")
        if raw_tasks is None:
            issues.append(_err(ERROR_MISSING_FIELD, "missing required field 'tasks'", where))
        elif not isinstance(raw_tasks, list):
            issues.append(
                _err(
                    ERROR_WRONG_TYPE,
                    f"field 'tasks' must be a list, got {type(raw_tasks).__name__}",
                    where,
                )
            )
        else:
            if not raw_tasks:
                issues.append(_err(ERROR_EMPTY_COLLECTION, "field 'tasks' must be non-empty", where))
            for index, raw_task in enumerate(raw_tasks):
                entry = _parse_task(raw_task, f"tasks[{index}]", issues)
                if entry is not None:
                    tasks.append(entry)

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return cls(story_id=story_id, tasks=tuple(tasks)), result

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"tasks": [task.to_dict() for task in self.tasks]}
        if self.story_id is not None:
            result["story_id"] = self.story_id
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def _parse_task(raw: Any, location: str, issues: list[ValidationIssue]) -> TaskEntry | None:
    if not isinstance(raw, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(raw).__name__}", location)
        )
        return None
    mapping = dict(raw)

    if "id" not in mapping and "task_id" in mapping:
        issues.append(_warn(WARNING_LEGACY_TASK_ID, "task uses legacy 'task_id'; mapped to 'id'", location))
        mapping["id"] = mapping.get("task_id")
    task_id = _get_str(mapping, "id", location, issues, required=True)
    title = _get_str(mapping, "title", location, issues, required=True)

    if "ac_mapping" not in mapping and "acceptance_criteria_mapped" in mapping:
        issues.append(
            _warn(
                WARNING_LEGACY_ACCEPTANCE_CRITERIA_MAPPED,
                "task uses legacy 'acceptance_criteria_mapped'; mapped to 'ac_mapping'",
                location,
            )
        )
        mapping["ac_mapping"] = mapping.get("acceptance_criteria_mapped")
    ac_mapping = _get_str_tuple(
        mapping, "ac_mapping", location, issues, required=True, allow_empty=False
    )

    description = _get_str(
        mapping, "description", location, issues, required=False, default="", allow_empty=True
    )
    dependencies = _get_str_tuple(mapping, "dependencies", location, issues)
    definition_of_done = _get_str_tuple(mapping, "definition_of_done", location, issues)
    priority = _get_opt_str_enum(
        mapping, "priority", location, issues, valid_values=_VALID_TASK_PRIORITIES
    )

    if "complexity" not in mapping and "estimated_complexity" in mapping:
        issues.append(
            _warn(
                WARNING_LEGACY_ESTIMATED_COMPLEXITY,
                "task uses legacy 'estimated_complexity'; mapped to 'complexity'",
                location,
            )
        )
        mapping["complexity"] = mapping.get("estimated_complexity")
    complexity = _get_opt_str_enum(
        mapping, "complexity", location, issues, valid_values=_VALID_TASK_COMPLEXITIES
    )

    return TaskEntry(
        id=task_id,
        title=title,
        ac_mapping=ac_mapping,
        description=description,
        dependencies=dependencies,
        priority=priority,
        complexity=complexity,
        definition_of_done=definition_of_done,
    )


# ---------------------------------------------------------------------------
# Assignment.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AssignmentArtifact(PlanningArtifact):
    """Task-to-UoW assignment (`{change_id}/planning/assignments.json`)."""

    ARTIFACT_TYPE: ClassVar[str] = "assignment"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.assignment"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "task-assignment"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("execution", "qa", "pr-review")
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/planning/assignments.json"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id",)
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_JSON

    batches: tuple[BatchEntry, ...]
    story_id: str | None = None

    @classmethod
    def _parse(cls, data: Any) -> tuple["AssignmentArtifact | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        story_id = _get_opt_str(mapping, "story_id", where, issues)

        raw_batches = mapping.get("batches")
        if raw_batches is None and "execution_schedule" in mapping:
            issues.append(
                _warn(
                    WARNING_LEGACY_EXECUTION_SCHEDULE,
                    "assignments uses legacy 'execution_schedule'; mapped to 'batches'",
                    where,
                )
            )
            raw_batches = mapping.get("execution_schedule")

        batches: list[BatchEntry] = []
        if raw_batches is None:
            issues.append(_err(ERROR_MISSING_FIELD, "missing required field 'batches'", where))
        elif not isinstance(raw_batches, list):
            issues.append(
                _err(
                    ERROR_WRONG_TYPE,
                    f"field 'batches' must be a list, got {type(raw_batches).__name__}",
                    where,
                )
            )
        else:
            if not raw_batches:
                issues.append(_err(ERROR_EMPTY_COLLECTION, "field 'batches' must be non-empty", where))
            for index, raw_batch in enumerate(raw_batches):
                entry = _parse_batch(raw_batch, f"batches[{index}]", issues)
                if entry is not None:
                    batches.append(entry)

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return cls(story_id=story_id, batches=tuple(batches)), result

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"batches": [batch.to_dict() for batch in self.batches]}
        if self.story_id is not None:
            result["story_id"] = self.story_id
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def _parse_batch(raw: Any, location: str, issues: list[ValidationIssue]) -> BatchEntry | None:
    if not isinstance(raw, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(raw).__name__}", location)
        )
        return None
    mapping = dict(raw)

    if "batch_id" not in mapping and "batch" in mapping:
        issues.append(_warn(WARNING_LEGACY_BATCH_KEY, "batch uses legacy 'batch'; mapped to 'batch_id'", location))
        mapping["batch_id"] = mapping.get("batch")

    batch_id: int | None
    raw_batch_id = mapping.get("batch_id")
    if raw_batch_id is None:
        issues.append(_err(ERROR_MISSING_FIELD, "missing required field 'batch_id'", location))
        batch_id = None
    elif isinstance(raw_batch_id, bool) or not isinstance(raw_batch_id, int):
        issues.append(
            _err(
                ERROR_WRONG_TYPE,
                f"field 'batch_id' must be an integer, got {type(raw_batch_id).__name__}",
                location,
            )
        )
        batch_id = None
    else:
        batch_id = raw_batch_id

    uows: list[UowEntry] = []
    raw_uows = mapping.get("uows")
    if raw_uows is None:
        issues.append(_err(ERROR_MISSING_FIELD, "missing required field 'uows'", location))
    elif not isinstance(raw_uows, list):
        issues.append(
            _err(ERROR_WRONG_TYPE, f"field 'uows' must be a list, got {type(raw_uows).__name__}", location)
        )
    else:
        if not raw_uows:
            issues.append(_err(ERROR_EMPTY_COLLECTION, "field 'uows' must be non-empty", location))
        for index, raw_uow in enumerate(raw_uows):
            entry = _parse_uow(raw_uow, f"{location}.uows[{index}]", issues)
            if entry is not None:
                uows.append(entry)

    parallel_execution = mapping.get("parallel_execution", False)
    if not isinstance(parallel_execution, bool):
        issues.append(
            _err(
                ERROR_WRONG_TYPE,
                f"field 'parallel_execution' must be a boolean, got {type(parallel_execution).__name__}",
                location,
            )
        )
        parallel_execution = False

    batch_rationale = _get_opt_str(mapping, "batch_rationale", location, issues)

    if batch_id is None:
        return None
    return BatchEntry(
        batch_id=batch_id,
        uows=tuple(uows),
        parallel_execution=parallel_execution,
        batch_rationale=batch_rationale,
    )


def _parse_uow(raw: Any, location: str, issues: list[ValidationIssue]) -> UowEntry | None:
    if not isinstance(raw, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(raw).__name__}", location)
        )
        return None
    mapping = dict(raw)

    uow_id = _get_str(mapping, "uow_id", location, issues, required=True)
    source_task_id = _get_str(mapping, "source_task_id", location, issues, required=True)
    title = _get_opt_str(mapping, "title", location, issues)
    assigned_role = _get_opt_str(mapping, "assigned_role", location, issues)
    priority_in_batch = _get_opt_int(mapping, "priority_in_batch", location, issues)
    rationale = _get_opt_str(mapping, "rationale", location, issues)
    dependencies = _get_str_tuple(mapping, "dependencies", location, issues)
    definition_of_done = _get_str_tuple(mapping, "definition_of_done", location, issues)

    return UowEntry(
        uow_id=uow_id,
        source_task_id=source_task_id,
        title=title,
        assigned_role=assigned_role,
        priority_in_batch=priority_in_batch,
        rationale=rationale,
        dependencies=dependencies,
        definition_of_done=definition_of_done,
    )


# ---------------------------------------------------------------------------
# UoW spec.
# ---------------------------------------------------------------------------

# Canonical fields whose absence marks a partial (legacy) UoW spec.
_UOW_SPEC_CANONICAL_FIELDS = (
    "source_task_id",
    "change_id",
    "story_id",
    "assigned_role",
    "title",
)


@dataclass(frozen=True, kw_only=True)
class UowSpecArtifact(PlanningArtifact):
    """Materialized UoW spec (`{change_id}/execution/{uow_id}/uow_spec.yaml`).

    Only `uow_id` is required. Partial historical specs (missing some canonical
    fields) are accepted with a `legacy_partial_uow_spec` warning rather than
    rejected, matching what current repository consumers tolerate.
    """

    ARTIFACT_TYPE: ClassVar[str] = "uow_spec"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.uow-spec"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "task-assignment"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("execution",)
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/execution/{uow_id}/uow_spec.yaml"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id", "uow_id")
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_YAML

    uow_id: str
    source_task_id: str | None = None
    change_id: str | None = None
    story_id: str | None = None
    assigned_role: str | None = None
    title: str | None = None
    description: str = ""
    ac_mapping: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    definition_of_done: tuple[str, ...] = ()
    implementation_hints: tuple[str, ...] = ()
    priority: str | None = None
    complexity: str | None = None

    @classmethod
    def _parse(cls, data: Any) -> tuple["UowSpecArtifact | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        uow_id = _get_str(mapping, "uow_id", where, issues, required=True)
        source_task_id = _get_opt_str(mapping, "source_task_id", where, issues)
        change_id = _get_opt_str(mapping, "change_id", where, issues)
        story_id = _get_opt_str(mapping, "story_id", where, issues)
        assigned_role = _get_opt_str(mapping, "assigned_role", where, issues)
        title = _get_opt_str(mapping, "title", where, issues)
        description = _get_str(
            mapping, "description", where, issues, required=False, default="", allow_empty=True
        )
        ac_mapping = _get_str_tuple(mapping, "ac_mapping", where, issues)
        dependencies = _get_str_tuple(mapping, "dependencies", where, issues)
        definition_of_done = _get_str_tuple(mapping, "definition_of_done", where, issues)
        implementation_hints = _get_str_tuple(mapping, "implementation_hints", where, issues)
        priority = _get_opt_str(mapping, "priority", where, issues)
        complexity = _get_opt_str(mapping, "complexity", where, issues)

        candidates = {
            "source_task_id": source_task_id,
            "change_id": change_id,
            "story_id": story_id,
            "assigned_role": assigned_role,
            "title": title,
        }
        missing = [name for name in _UOW_SPEC_CANONICAL_FIELDS if candidates[name] is None]
        if missing:
            issues.append(
                _warn(
                    WARNING_LEGACY_PARTIAL_UOW_SPEC,
                    f"partial uow_spec; missing canonical fields: {', '.join(missing)}",
                    where,
                )
            )

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return (
            cls(
                uow_id=uow_id,
                source_task_id=source_task_id,
                change_id=change_id,
                story_id=story_id,
                assigned_role=assigned_role,
                title=title,
                description=description,
                ac_mapping=ac_mapping,
                dependencies=dependencies,
                definition_of_done=definition_of_done,
                implementation_hints=implementation_hints,
                priority=priority,
                complexity=complexity,
            ),
            result,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"uow_id": self.uow_id}
        if self.source_task_id is not None:
            result["source_task_id"] = self.source_task_id
        if self.change_id is not None:
            result["change_id"] = self.change_id
        if self.story_id is not None:
            result["story_id"] = self.story_id
        if self.assigned_role is not None:
            result["assigned_role"] = self.assigned_role
        if self.title is not None:
            result["title"] = self.title
        if self.description:
            result["description"] = self.description
        if self.ac_mapping:
            result["ac_mapping"] = list(self.ac_mapping)
        if self.dependencies:
            result["dependencies"] = list(self.dependencies)
        if self.definition_of_done:
            result["definition_of_done"] = list(self.definition_of_done)
        if self.implementation_hints:
            result["implementation_hints"] = list(self.implementation_hints)
        if self.priority is not None:
            result["priority"] = self.priority
        if self.complexity is not None:
            result["complexity"] = self.complexity
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# Stable, ordered registry of the four planning-artifact contracts.
PLANNING_ARTIFACTS: tuple[type[PlanningArtifact], ...] = (
    StoryArtifact,
    TaskPlanArtifact,
    AssignmentArtifact,
    UowSpecArtifact,
)


# ---------------------------------------------------------------------------
# Implementation report (P20).
# ---------------------------------------------------------------------------

_IMPL_REPORT_RECOGNIZED_KEYS = frozenset(
    {
        "schema_version",
        "uow_id",
        "status",
        "implementation_summary",
        "definition_of_done_status",
        "definition_of_done",
        "change_id",
        "story_id",
    }
)


def _parse_dod_item(raw: Any, location: str, issues: list[ValidationIssue]) -> DefinitionOfDoneItem | None:
    if not isinstance(raw, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(raw).__name__}", location)
        )
        return None
    mapping = dict(raw)
    item = _get_str(mapping, "item", location, issues, required=True)
    evidence = _get_opt_str(mapping, "evidence", location, issues)
    met = _get_bool(mapping, "met", location, issues, required=True)
    if met is None:
        return None
    return DefinitionOfDoneItem(item=item, met=met, evidence=evidence)


def _parse_dod_list(raw: Any, where: str, issues: list[ValidationIssue]) -> tuple[DefinitionOfDoneItem, ...]:
    if raw is None:
        issues.append(
            _err(ERROR_MISSING_FIELD, "missing required field 'definition_of_done_status'", where)
        )
        return ()
    if not isinstance(raw, list):
        issues.append(
            _err(
                ERROR_WRONG_TYPE,
                f"field 'definition_of_done_status' must be a list, got {type(raw).__name__}",
                where,
            )
        )
        return ()
    if not raw:
        issues.append(
            _err(ERROR_EMPTY_COLLECTION, "field 'definition_of_done_status' must be non-empty", where)
        )
    items: list[DefinitionOfDoneItem] = []
    for index, entry in enumerate(raw):
        parsed = _parse_dod_item(entry, f"{where}.definition_of_done_status[{index}]", issues)
        if parsed is not None:
            items.append(parsed)
    return tuple(items)


@dataclass(frozen=True, kw_only=True)
class ImplementationReportPayload(PlanningArtifact):
    """Implementation report (`{change_id}/execution/{uow_id}/impl_report.yaml`).

    Represents the stable, currently-validated fields of the legacy
    `impl_report.yaml` artifact (see `scripts/validate-artifact-schema.py` and
    `agent-definition-source/software-engineer/v2/prompt.md`). The many
    evolving report sections generated by the software-engineer agent
    (`engineering_scope_classification`, `files_modified`, `tests_written`,
    `commands_executed`, `no_mistakes_gate`, `worktree_management`,
    `risks_identified`, `implementation_decisions`, …) are preserved as
    `extension` data, not modeled as contract fields.
    """

    ARTIFACT_TYPE: ClassVar[str] = "impl_report"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.impl-report"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "execution"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ()
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/execution/{uow_id}/impl_report.yaml"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id", "uow_id")
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_YAML

    schema_version: str
    uow_id: str
    status: str
    implementation_summary: str
    definition_of_done_status: tuple[DefinitionOfDoneItem, ...]
    change_id: str | None = None
    story_id: str | None = None
    extension: Mapping[str, Any] = MappingProxyType({})

    @classmethod
    def _parse(cls, data: Any) -> tuple["ImplementationReportPayload | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        schema_version = _get_schema_version(mapping, where, issues)
        uow_id = _get_str(mapping, "uow_id", where, issues, required=True)
        status = _get_str_enum(
            mapping, "status", where, issues, valid_values=_VALID_IMPL_STATUSES, required=True
        )
        implementation_summary = _get_str(
            mapping, "implementation_summary", where, issues, required=True
        )

        raw_dod = mapping.get("definition_of_done_status")
        if raw_dod is None and "definition_of_done" in mapping:
            issues.append(
                _warn(
                    WARNING_LEGACY_DEFINITION_OF_DONE,
                    "impl_report uses legacy 'definition_of_done'; mapped to 'definition_of_done_status'",
                    where,
                )
            )
            raw_dod = mapping.get("definition_of_done")
        definition_of_done_status = _parse_dod_list(raw_dod, where, issues)

        change_id = _get_opt_str(mapping, "change_id", where, issues)
        story_id = _get_opt_str(mapping, "story_id", where, issues)

        extension = _extract_extension(mapping, _IMPL_REPORT_RECOGNIZED_KEYS)

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return (
            cls(
                schema_version=schema_version,
                uow_id=uow_id,
                status=status,
                implementation_summary=implementation_summary,
                definition_of_done_status=definition_of_done_status,
                change_id=change_id,
                story_id=story_id,
                extension=extension,
            ),
            result,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "uow_id": self.uow_id,
            "status": self.status,
            "implementation_summary": self.implementation_summary,
            "definition_of_done_status": [item.to_dict() for item in self.definition_of_done_status],
        }
        if self.change_id is not None:
            result["change_id"] = self.change_id
        if self.story_id is not None:
            result["story_id"] = self.story_id
        result.update(_thaw_extension_value(self.extension))
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_artifact_ref(
        self,
        *,
        path: str | Path | None = None,
        uri: str | None = None,
        validation_status: ArtifactValidationStatus | str | None = None,
        checksum_sha256: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Populate stable identity metadata (`uow_id`/`status`/ids) automatically.

        Caller-supplied `metadata` keys take precedence over these defaults.
        """
        combined: dict[str, Any] = {"uow_id": self.uow_id, "status": self.status}
        if self.change_id is not None:
            combined["change_id"] = self.change_id
        if self.story_id is not None:
            combined["story_id"] = self.story_id
        if metadata is not None:
            combined.update(metadata)
        return super().to_artifact_ref(
            path=path,
            uri=uri,
            validation_status=validation_status,
            checksum_sha256=checksum_sha256,
            metadata=combined,
        )


def load_implementation_report(path: str | Path) -> ImplementationReportPayload:
    """Read-only load of an implementation report, discarding warnings."""
    return ImplementationReportPayload.load(path)


# ---------------------------------------------------------------------------
# QA report (P20).
# ---------------------------------------------------------------------------

_QA_REPORT_RECOGNIZED_KEYS = frozenset(
    {
        "schema_version",
        "story_id",
        "qa_status",
        "acceptance_criteria_validation",
        "final_recommendation",
        "conditions",
    }
)


def _parse_qa_ac_entry(
    ac_id: Any, raw: Any, where: str, issues: list[ValidationIssue]
) -> QAAcValidation | None:
    location = f"{where}.acceptance_criteria_validation[{ac_id!r}]"
    if not isinstance(ac_id, str) or not ac_id.strip():
        issues.append(
            _err(
                ERROR_INVALID_VALUE,
                f"acceptance_criteria_validation key must be a non-empty string, got {ac_id!r}",
                where,
            )
        )
        return None
    if not isinstance(raw, Mapping):
        issues.append(
            _err(ERROR_NOT_A_MAPPING, f"expected a mapping, got {type(raw).__name__}", location)
        )
        return None
    mapping = dict(raw)

    status = _get_str_enum(
        mapping, "status", location, issues, valid_values=_VALID_AC_VALIDATION_STATUSES, required=True
    )
    validation_method = _get_opt_str(mapping, "validation_method", location, issues)
    notes = _get_opt_str(mapping, "notes", location, issues)

    evidence_type: str | None = None
    evidence_reference: str | None = None
    raw_evidence = mapping.get("evidence")
    if raw_evidence is not None:
        if not isinstance(raw_evidence, Mapping):
            issues.append(
                _err(
                    ERROR_NOT_A_MAPPING,
                    f"expected a mapping, got {type(raw_evidence).__name__}",
                    f"{location}.evidence",
                )
            )
        else:
            evidence_mapping = dict(raw_evidence)
            evidence_type = _get_opt_str(evidence_mapping, "type", f"{location}.evidence", issues)
            evidence_reference = _get_opt_str(
                evidence_mapping, "reference", f"{location}.evidence", issues
            )

    if status is None:
        return None
    return QAAcValidation(
        ac_id=ac_id,
        status=status,
        validation_method=validation_method,
        evidence_type=evidence_type,
        evidence_reference=evidence_reference,
        notes=notes,
    )


def _parse_ac_validation_map(
    raw: Any, where: str, issues: list[ValidationIssue]
) -> tuple[QAAcValidation, ...]:
    if raw is None:
        issues.append(
            _err(ERROR_MISSING_FIELD, "missing required field 'acceptance_criteria_validation'", where)
        )
        return ()
    if not isinstance(raw, Mapping):
        issues.append(
            _err(
                ERROR_WRONG_TYPE,
                f"field 'acceptance_criteria_validation' must be a mapping, got {type(raw).__name__}",
                where,
            )
        )
        return ()
    if not raw:
        issues.append(
            _err(ERROR_EMPTY_COLLECTION, "field 'acceptance_criteria_validation' must be non-empty", where)
        )
    entries: list[QAAcValidation] = []
    for ac_id, value in raw.items():
        entry = _parse_qa_ac_entry(ac_id, value, where, issues)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


@dataclass(frozen=True, kw_only=True)
class QAReportPayload(PlanningArtifact):
    """QA report (`{change_id}/qa/qa_report.yaml`).

    Represents the stable, currently-validated fields of the legacy
    `qa_report.yaml` artifact (see `agent-definition-source/qa/v2/prompt.md`).
    Rich optional sections (`regression_risk_assessment`, `issues_found`,
    `release_notes`, `evidence_manifest`, `knowledge_summary`,
    `metacognitive_context`, …) are preserved as `extension` data, not modeled
    as contract fields.
    """

    ARTIFACT_TYPE: ClassVar[str] = "qa_report"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.qa-report"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = "1"
    PRODUCER_STAGE: ClassVar[str] = "qa"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ()
    PATH_SCOPE: ClassVar[str] = "agent_context"
    RELATIVE_PATH_TEMPLATE: ClassVar[str] = "{change_id}/qa/qa_report.yaml"
    PATH_PARAMETERS: ClassVar[tuple[str, ...]] = ("change_id",)
    CONTENT_FORMAT: ClassVar[str] = _FORMAT_YAML

    schema_version: str
    story_id: str
    qa_status: str
    acceptance_criteria_validation: tuple[QAAcValidation, ...]
    final_recommendation: str
    conditions: tuple[str, ...] = ()
    extension: Mapping[str, Any] = MappingProxyType({})

    @classmethod
    def _parse(cls, data: Any) -> tuple["QAReportPayload | None", ValidationResult]:
        where = cls.__name__
        issues: list[ValidationIssue] = []
        mapping = _require_mapping(data, where, issues)
        if mapping is None:
            return None, ValidationResult(issues=tuple(issues))

        schema_version = _get_schema_version(mapping, where, issues)
        story_id = _get_str(mapping, "story_id", where, issues, required=True)
        qa_status = _get_str_enum(
            mapping, "qa_status", where, issues, valid_values=_VALID_QA_STATUSES, required=True
        )
        acceptance_criteria_validation = _parse_ac_validation_map(
            mapping.get("acceptance_criteria_validation"), where, issues
        )
        final_recommendation = _get_str_enum(
            mapping,
            "final_recommendation",
            where,
            issues,
            valid_values=_VALID_FINAL_RECOMMENDATIONS,
            required=True,
        )
        conditions = _get_str_tuple(mapping, "conditions", where, issues)
        if final_recommendation == "approve_with_conditions" and not conditions:
            issues.append(
                _err(
                    ERROR_MISSING_FIELD,
                    "field 'conditions' must be non-empty when final_recommendation is "
                    "'approve_with_conditions'",
                    where,
                )
            )

        extension = _extract_extension(mapping, _QA_REPORT_RECOGNIZED_KEYS)

        result = ValidationResult(issues=tuple(issues))
        if not result.ok:
            return None, result
        return (
            cls(
                schema_version=schema_version,
                story_id=story_id,
                qa_status=qa_status,
                acceptance_criteria_validation=acceptance_criteria_validation,
                final_recommendation=final_recommendation,
                conditions=conditions,
                extension=extension,
            ),
            result,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "story_id": self.story_id,
            "qa_status": self.qa_status,
            "acceptance_criteria_validation": {
                entry.ac_id: entry.to_dict() for entry in self.acceptance_criteria_validation
            },
            "final_recommendation": self.final_recommendation,
        }
        if self.conditions:
            result["conditions"] = list(self.conditions)
        result.update(_thaw_extension_value(self.extension))
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_artifact_ref(
        self,
        *,
        path: str | Path | None = None,
        uri: str | None = None,
        validation_status: ArtifactValidationStatus | str | None = None,
        checksum_sha256: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        """Populate stable identity metadata (`story_id`/`qa_status`/…) automatically.

        Caller-supplied `metadata` keys take precedence over these defaults.
        """
        combined: dict[str, Any] = {
            "story_id": self.story_id,
            "qa_status": self.qa_status,
            "final_recommendation": self.final_recommendation,
        }
        if metadata is not None:
            combined.update(metadata)
        return super().to_artifact_ref(
            path=path,
            uri=uri,
            validation_status=validation_status,
            checksum_sha256=checksum_sha256,
            metadata=combined,
        )


def load_qa_report(path: str | Path) -> QAReportPayload:
    """Read-only load of a QA report, discarding warnings."""
    return QAReportPayload.load(path)
