"""Canonical, versioned machine-readable model for a v0.2 report comparison.

This schema is deliberately versioned independently of `eval.report_schema`'s
`REPORT_SCHEMA_VERSION` ("0.2") — the *comparison* output format has its own
lifecycle and starts at `1.0`. Bumping this version is required whenever the
shape of `ComparisonResult` changes in a way that breaks existing consumers.

Serialization follows the same convention as `eval/report_schema.py`: plain
dataclasses, `str, Enum` members, a recursive `_to_jsonable` helper, and a
`validate_payload`-style function that never raises. There is no `from_dict`
reconstruction here (Prompt 5 relies on schema validation + round-trip JSON
purity tests rather than full deserialization — see
`docs/evaluation-comparison.md` "Design decisions").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

COMPARISON_SCHEMA_VERSION = "1.0"


class CompareInputError(Exception):
    """Raised when baseline/candidate input or policy configuration is invalid.

    This is distinct from a `Classification` — an input/schema failure never
    produces an `inconclusive` (or any other) comparison result; it aborts
    before a `ComparisonResult` is built at all.
    """

    def __init__(self, message: str, *, side: str, errors: list[str] | None = None):
        self.side = side
        self.errors = errors or [message]
        super().__init__(f"[{side}] {message}")


class Classification(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    MIXED = "mixed"
    UNCHANGED = "unchanged"
    INCONCLUSIVE = "inconclusive"


class ChangeCategory(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"
    INCOMPARABLE = "incomparable"


class Comparability(str, Enum):
    COMPARABLE = "comparable"
    DEGRADED = "degraded"
    INCOMPARABLE = "incomparable"


class DriftSeverity(str, Enum):
    INFORMATIONAL = "informational"
    MATERIAL = "material"
    INCOMPATIBLE = "incompatible"


class ReasonSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DeltaAvailability(str, Enum):
    COMPUTED = "computed"
    UNAVAILABLE = "unavailable"
    INCOMPARABLE = "incomparable"


def _to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return {f: _to_jsonable(getattr(obj, f)) for f in obj.__dataclass_fields__}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError(f"Object of type {type(obj)!r} is not JSON-serializable by comparison_schema")


@dataclass
class ReportIdentity:
    """Normalized identity for one side of a comparison.

    Every field is either the value present in the report or `None` when the
    report did not carry that field. `None` is never a fabricated value —
    it means "absent from this report", and renderers must display it as
    such rather than omitting it silently.
    """

    label: str | None
    eval_run_id: str | None
    created_at: str | None
    candidate_version: str | None
    baseline_version: str | None
    benchmark_suite_id: str | None
    report_schema_version: str | None
    trial_total: int
    # Fixed, documented metadata keys (see ComparisonPolicy.identity_metadata_keys).
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class MetricDelta:
    """Delta between a baseline and candidate `Metric`.

    Rules (see docs "Metric-delta semantics"):
    - Computed only when *both* sides are `known`.
    - `absolute_delta` is computed even when the baseline value is 0 — a
      zero baseline never suppresses the absolute delta.
    - `relative_delta` is set to `None` (with `reason` explaining why)
      whenever the baseline value is 0, since a percentage change from zero
      is undefined/misleading. This is the only case where availability
      stays COMPUTED but relative_delta is None.
    - When either side is not `known`, availability is UNAVAILABLE and
      `reason` names which side and why (never coerced to 0).
    """

    name: str
    baseline_status: str
    baseline_value: Any
    candidate_status: str
    candidate_value: Any
    absolute_delta: float | int | None
    relative_delta: float | None
    availability: DeltaAvailability
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class AcDelta:
    benchmark_id: str
    ac_id: str
    critical: bool
    baseline_status: str | None
    candidate_status: str | None
    baseline_difficulty: str | None
    candidate_difficulty: str | None
    category: ChangeCategory
    incomparable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class BenchmarkDelta:
    benchmark_id: str
    baseline_difficulty: str | None
    candidate_difficulty: str | None
    baseline_status: str | None
    candidate_status: str | None
    baseline_ac_pass_rate: float | None
    candidate_ac_pass_rate: float | None
    baseline_trial_count: int | None
    candidate_trial_count: int | None
    trial_count_comparable: bool
    category: ChangeCategory
    newly_failing: bool
    resolved_failure: bool
    easy_regression: bool
    incomparable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class EvidenceFieldDelta:
    field: str
    requirement: str  # "required" | "conditional" | "optional"
    baseline_status: str
    baseline_value: bool | None
    candidate_status: str
    candidate_value: bool | None
    regressed: bool  # known True -> known False (explicit contract failure)
    degraded: bool  # known -> unavailable (missing evidence, not a proven failure)
    gained: bool

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class DriftFinding:
    field: str
    baseline_value: Any
    candidate_value: Any
    severity: DriftSeverity
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class ClassificationReason:
    code: str
    severity: ReasonSeverity
    summary: str
    benchmark_ids: list[str] = field(default_factory=list)
    ac_ids: list[str] = field(default_factory=list)
    metric_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


@dataclass
class ComparisonResult:
    comparison_schema_version: str
    baseline_identity: ReportIdentity
    candidate_identity: ReportIdentity

    comparability: Comparability
    comparability_reasons: list[str]
    drift: list[DriftFinding]

    ac_deltas: list[AcDelta]
    improved_acs: list[AcDelta]
    regressed_acs: list[AcDelta]
    unchanged_acs: list[AcDelta]
    added_acs: list[AcDelta]
    removed_acs: list[AcDelta]
    incomparable_acs: list[AcDelta]
    critical_ac_regressions: list[AcDelta]
    critical_ac_improvements: list[AcDelta]

    benchmark_deltas: list[BenchmarkDelta]
    improved_benchmarks: list[BenchmarkDelta]
    regressed_benchmarks: list[BenchmarkDelta]
    unchanged_benchmarks: list[BenchmarkDelta]
    added_benchmarks: list[BenchmarkDelta]
    removed_benchmarks: list[BenchmarkDelta]
    incomparable_benchmarks: list[BenchmarkDelta]
    easy_regressions: list[BenchmarkDelta]
    new_failures: list[BenchmarkDelta]
    resolved_failures: list[BenchmarkDelta]

    scorecard_deltas: dict[str, dict[str, MetricDelta]]

    evidence_deltas: list[EvidenceFieldDelta]
    evidence_regressions: list[EvidenceFieldDelta]

    classification: Classification
    reasons: list[ClassificationReason]
    policy: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)


_REQUIRED_TOP_LEVEL = (
    "comparison_schema_version",
    "baseline_identity",
    "candidate_identity",
    "comparability",
    "classification",
    "reasons",
)

_VALID_CLASSIFICATIONS = {c.value for c in Classification}
_VALID_COMPARABILITY = {c.value for c in Comparability}


def validate_comparison_payload(data: dict[str, Any]) -> list[str]:
    """Validate a serialized `ComparisonResult` dict. Never raises.

    This is intentionally a schema-shape check (required top-level keys,
    valid enum values, list-typed collections) rather than a full
    `from_dict` reconstruction — see docs for rationale. It is sufficient to
    catch malformed/foreign payloads and to support round-trip tests.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["comparison payload must be a JSON object"]
    for key in _REQUIRED_TOP_LEVEL:
        if key not in data:
            errors.append(f"missing required field: {key}")
    if "comparison_schema_version" in data and data["comparison_schema_version"] != COMPARISON_SCHEMA_VERSION:
        errors.append(
            f"unsupported comparison_schema_version: {data['comparison_schema_version']!r} "
            f"(expected {COMPARISON_SCHEMA_VERSION!r})"
        )
    if "classification" in data and data["classification"] not in _VALID_CLASSIFICATIONS:
        errors.append(f"invalid classification: {data['classification']!r}")
    if "comparability" in data and data["comparability"] not in _VALID_COMPARABILITY:
        errors.append(f"invalid comparability: {data['comparability']!r}")
    list_fields = (
        "drift",
        "ac_deltas",
        "improved_acs",
        "regressed_acs",
        "unchanged_acs",
        "added_acs",
        "removed_acs",
        "incomparable_acs",
        "critical_ac_regressions",
        "critical_ac_improvements",
        "benchmark_deltas",
        "improved_benchmarks",
        "regressed_benchmarks",
        "unchanged_benchmarks",
        "added_benchmarks",
        "removed_benchmarks",
        "incomparable_benchmarks",
        "easy_regressions",
        "new_failures",
        "resolved_failures",
        "evidence_deltas",
        "evidence_regressions",
        "reasons",
        "warnings",
        "comparability_reasons",
    )
    for key in list_fields:
        if key in data and not isinstance(data[key], list):
            errors.append(f"field {key!r} must be a list")
    if "scorecard_deltas" in data and not isinstance(data["scorecard_deltas"], dict):
        errors.append("field 'scorecard_deltas' must be an object")
    return errors
