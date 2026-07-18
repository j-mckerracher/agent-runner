"""Versioned, machine-readable evaluation report schema (v0.2).

This module defines the report *contract* — the shape of data an eval run
should be able to produce — independent of how `eval/runner.py` currently
generates its report. See `docs/evaluation-contract.md` for the rationale
and hierarchy (`suite -> case -> trial -> acceptance criteria`).

Nothing here changes existing eval/workflow behavior. `eval/runner.py`
continues to emit its current report shape (`eval/result_schema.py`); this
module is additive and is *not yet* wired into the runner's default output.

Missing metrics (tokens, cost, wall-clock duration, trace presence, ...)
are never silently coerced to `0`. Every such value is wrapped in `Metric`,
which carries an explicit `MetricStatus` alongside the value so "measured
as zero" is distinguishable from "unknown" / "not applicable" / "not
collected".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any

REPORT_SCHEMA_VERSION = "0.2"


class ReportValidationError(Exception):
    """Raised when an EvalReport payload fails structural validation.

    Carries the full list of problems found (not just the first one) so
    callers can report everything wrong with a payload in one pass.
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid report")


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class MetricStatus(str, Enum):
    """Honesty flag for a metric value.

    KNOWN: `value` holds a real measurement (which may legitimately be 0).
    UNKNOWN: the metric was expected but its value could not be determined.
    NOT_APPLICABLE: the metric does not apply to this result at all.
    NOT_COLLECTED: instrumentation for this metric was not enabled/run.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    NOT_COLLECTED = "not_collected"


class AcStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


class TrialStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class CaseStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    ERROR = "error"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


_VALID_ENUM_VALUES = {
    "AcStatus": {member.value for member in AcStatus},
    "TrialStatus": {member.value for member in TrialStatus},
    "CaseStatus": {member.value for member in CaseStatus},
    "MetricStatus": {member.value for member in MetricStatus},
}


# --------------------------------------------------------------------------
# Serialization helpers
# --------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_jsonable(value) for key, value in obj.items()}
    return obj


def _require(data: dict[str, Any], key: str, errors: list[str], where: str) -> Any:
    if key not in data or data[key] is None:
        errors.append(f"{where}: missing required field '{key}'")
        return None
    return data[key]


# --------------------------------------------------------------------------
# Metric: the honest-missing-value wrapper
# --------------------------------------------------------------------------


@dataclass
class Metric:
    status: MetricStatus
    value: float | int | bool | str | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = MetricStatus(self.status)

    @classmethod
    def known(cls, value: float | int | bool | str) -> "Metric":
        return cls(status=MetricStatus.KNOWN, value=value)

    @classmethod
    def unknown(cls, reason: str | None = None) -> "Metric":
        return cls(status=MetricStatus.UNKNOWN, unavailable_reason=reason)

    @classmethod
    def not_applicable(cls, reason: str | None = None) -> "Metric":
        return cls(status=MetricStatus.NOT_APPLICABLE, unavailable_reason=reason)

    @classmethod
    def not_collected(cls, reason: str | None = None) -> "Metric":
        return cls(status=MetricStatus.NOT_COLLECTED, unavailable_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metric":
        return cls(
            status=MetricStatus(data.get("status")),
            value=data.get("value"),
            unavailable_reason=data.get("unavailable_reason"),
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: metric must be an object, got {type(data).__name__}"]
        status = data.get("status")
        if status not in _VALID_ENUM_VALUES["MetricStatus"]:
            errors.append(f"{where}: invalid metric status '{status}'")
            return errors
        value = data.get("value")
        if status == MetricStatus.KNOWN.value and value is None:
            errors.append(f"{where}: metric status is 'known' but value is None")
        if status != MetricStatus.KNOWN.value and value is not None:
            errors.append(f"{where}: metric status is '{status}' but value is set ({value!r}); missing metrics must not carry a value")
        return errors


# --------------------------------------------------------------------------
# References (evidence / trace / artifact pointers)
# --------------------------------------------------------------------------


@dataclass
class Reference:
    ref_id: str
    uri: str
    description: str = ""
    kind: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reference":
        return cls(
            ref_id=data.get("ref_id", ""),
            uri=data.get("uri", ""),
            description=data.get("description", ""),
            kind=data.get("kind", "generic"),
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        if not isinstance(data, dict):
            return [f"{where}: reference must be an object, got {type(data).__name__}"]
        errors = []
        if not data.get("ref_id"):
            errors.append(f"{where}: reference missing 'ref_id'")
        if not data.get("uri"):
            errors.append(f"{where}: reference missing 'uri'")
        return errors


# --------------------------------------------------------------------------
# Acceptance criteria
# --------------------------------------------------------------------------


@dataclass
class AcceptanceCriteriaResult:
    ac_id: str
    description: str
    status: AcStatus
    critical: bool
    evidence: list[Reference] = field(default_factory=list)
    failure_reason: str | None = None
    related_tests: list[str] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = AcStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AcceptanceCriteriaResult":
        return cls(
            ac_id=data.get("ac_id", ""),
            description=data.get("description", ""),
            status=AcStatus(data.get("status")),
            critical=bool(data.get("critical", False)),
            evidence=[Reference.from_dict(item) for item in data.get("evidence", [])],
            failure_reason=data.get("failure_reason"),
            related_tests=data.get("related_tests"),
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: acceptance criteria result must be an object"]
        _require(data, "ac_id", errors, where)
        _require(data, "description", errors, where)
        status = data.get("status")
        if status not in _VALID_ENUM_VALUES["AcStatus"]:
            errors.append(f"{where}: invalid AC status '{status}'")
        if "critical" not in data:
            errors.append(f"{where}: missing required field 'critical'")
        for index, evidence in enumerate(data.get("evidence", []) or []):
            errors.extend(Reference.validate_payload(evidence, f"{where}.evidence[{index}]"))
        return errors


# --------------------------------------------------------------------------
# Trial results
# --------------------------------------------------------------------------


@dataclass
class TrialResult:
    trial_id: str
    benchmark_id: str
    status: TrialStatus
    started_at: str
    completed_at: str | None
    duration_ms: Metric
    workflow_result_ref: str | None = None
    trace_ref: str | None = None
    artifact_refs: list[Reference] = field(default_factory=list)
    test_result_ref: str | None = None
    failure_category: str | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = TrialStatus(self.status)
        if isinstance(self.duration_ms, dict):
            self.duration_ms = Metric.from_dict(self.duration_ms)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialResult":
        return cls(
            trial_id=data.get("trial_id", ""),
            benchmark_id=data.get("benchmark_id", ""),
            status=TrialStatus(data.get("status")),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at"),
            duration_ms=Metric.from_dict(data.get("duration_ms", {})),
            workflow_result_ref=data.get("workflow_result_ref"),
            trace_ref=data.get("trace_ref"),
            artifact_refs=[Reference.from_dict(item) for item in data.get("artifact_refs", [])],
            test_result_ref=data.get("test_result_ref"),
            failure_category=data.get("failure_category"),
            error_summary=data.get("error_summary"),
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: trial result must be an object"]
        _require(data, "trial_id", errors, where)
        _require(data, "benchmark_id", errors, where)
        _require(data, "started_at", errors, where)
        status = data.get("status")
        if status not in _VALID_ENUM_VALUES["TrialStatus"]:
            errors.append(f"{where}: invalid trial status '{status}'")
        errors.extend(Metric.validate_payload(data.get("duration_ms"), f"{where}.duration_ms"))
        for index, artifact in enumerate(data.get("artifact_refs", []) or []):
            errors.extend(Reference.validate_payload(artifact, f"{where}.artifact_refs[{index}]"))
        return errors


# --------------------------------------------------------------------------
# Scorecard
# --------------------------------------------------------------------------


@dataclass
class CapabilityScore:
    ac_pass_rate: Metric
    critical_ac_pass_rate: Metric
    hidden_test_pass_rate: Metric
    full_benchmark_pass_rate: Metric

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityScore":
        return cls(**{key: Metric.from_dict(data.get(key, {})) for key in _CAPABILITY_FIELDS})

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: capability score must be an object"]
        for name in _CAPABILITY_FIELDS:
            if name not in data:
                errors.append(f"{where}: missing '{name}'")
                continue
            errors.extend(Metric.validate_payload(data[name], f"{where}.{name}"))
        return errors


_CAPABILITY_FIELDS = ("ac_pass_rate", "critical_ac_pass_rate", "hidden_test_pass_rate", "full_benchmark_pass_rate")


@dataclass
class ReliabilityScore:
    trial_pass_rate: Metric
    variance: Metric
    flaky_case_count: Metric
    workflow_failure_rate: Metric

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReliabilityScore":
        return cls(**{key: Metric.from_dict(data.get(key, {})) for key in _RELIABILITY_FIELDS})

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: reliability score must be an object"]
        for name in _RELIABILITY_FIELDS:
            if name not in data:
                errors.append(f"{where}: missing '{name}'")
                continue
            errors.extend(Metric.validate_payload(data[name], f"{where}.{name}"))
        return errors


_RELIABILITY_FIELDS = ("trial_pass_rate", "variance", "flaky_case_count", "workflow_failure_rate")


@dataclass
class EfficiencyScore:
    wall_clock_duration_ms: Metric
    agent_invocation_count: Metric
    loop_iteration_count: Metric
    token_usage: Metric
    estimated_cost: Metric

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EfficiencyScore":
        return cls(**{key: Metric.from_dict(data.get(key, {})) for key in _EFFICIENCY_FIELDS})

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: efficiency score must be an object"]
        for name in _EFFICIENCY_FIELDS:
            if name not in data:
                errors.append(f"{where}: missing '{name}'")
                continue
            errors.extend(Metric.validate_payload(data[name], f"{where}.{name}"))
        return errors


_EFFICIENCY_FIELDS = ("wall_clock_duration_ms", "agent_invocation_count", "loop_iteration_count", "token_usage", "estimated_cost")


@dataclass
class TraceabilityScore:
    trace_present: Metric
    test_output_present: Metric
    final_artifact_refs_present: Metric
    final_diff_ref_present: Metric
    prompt_hashes_present: Metric

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceabilityScore":
        return cls(**{key: Metric.from_dict(data.get(key, {})) for key in _TRACEABILITY_FIELDS})

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: traceability score must be an object"]
        for name in _TRACEABILITY_FIELDS:
            if name not in data:
                errors.append(f"{where}: missing '{name}'")
                continue
            errors.extend(Metric.validate_payload(data[name], f"{where}.{name}"))
        return errors


_TRACEABILITY_FIELDS = ("trace_present", "test_output_present", "final_artifact_refs_present", "final_diff_ref_present", "prompt_hashes_present")


@dataclass
class Scorecard:
    capability: CapabilityScore
    reliability: ReliabilityScore
    efficiency: EfficiencyScore
    traceability: TraceabilityScore

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scorecard":
        return cls(
            capability=CapabilityScore.from_dict(data.get("capability", {})),
            reliability=ReliabilityScore.from_dict(data.get("reliability", {})),
            efficiency=EfficiencyScore.from_dict(data.get("efficiency", {})),
            traceability=TraceabilityScore.from_dict(data.get("traceability", {})),
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: scorecard must be an object"]
        for name, validator in (
            ("capability", CapabilityScore.validate_payload),
            ("reliability", ReliabilityScore.validate_payload),
            ("efficiency", EfficiencyScore.validate_payload),
            ("traceability", TraceabilityScore.validate_payload),
        ):
            if name not in data:
                errors.append(f"{where}: missing '{name}'")
                continue
            errors.extend(validator(data[name], f"{where}.{name}"))
        return errors


# --------------------------------------------------------------------------
# Benchmark case results
# --------------------------------------------------------------------------


@dataclass
class BenchmarkCaseResult:
    benchmark_id: str
    difficulty: str
    status: CaseStatus
    trial_results: list[TrialResult] = field(default_factory=list)
    aggregate_result: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria_results: list[AcceptanceCriteriaResult] = field(default_factory=list)
    evidence_references: list[Reference] = field(default_factory=list)
    domain: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = CaseStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkCaseResult":
        return cls(
            benchmark_id=data.get("benchmark_id", ""),
            difficulty=data.get("difficulty", ""),
            domain=data.get("domain"),
            status=CaseStatus(data.get("status")),
            trial_results=[TrialResult.from_dict(item) for item in data.get("trial_results", [])],
            aggregate_result=data.get("aggregate_result", {}) or {},
            acceptance_criteria_results=[AcceptanceCriteriaResult.from_dict(item) for item in data.get("acceptance_criteria_results", [])],
            evidence_references=[Reference.from_dict(item) for item in data.get("evidence_references", [])],
        )

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: benchmark case result must be an object"]
        _require(data, "benchmark_id", errors, where)
        _require(data, "difficulty", errors, where)
        status = data.get("status")
        if status not in _VALID_ENUM_VALUES["CaseStatus"]:
            errors.append(f"{where}: invalid case status '{status}'")
        for index, trial in enumerate(data.get("trial_results", []) or []):
            errors.extend(TrialResult.validate_payload(trial, f"{where}.trial_results[{index}]"))
        for index, ac in enumerate(data.get("acceptance_criteria_results", []) or []):
            errors.extend(AcceptanceCriteriaResult.validate_payload(ac, f"{where}.acceptance_criteria_results[{index}]"))
        for index, evidence in enumerate(data.get("evidence_references", []) or []):
            errors.extend(Reference.validate_payload(evidence, f"{where}.evidence_references[{index}]"))
        return errors


# --------------------------------------------------------------------------
# Top-level report
# --------------------------------------------------------------------------


@dataclass
class EvalReport:
    eval_run_id: str
    created_at: str
    candidate_version: str
    benchmark_suite_id: str
    benchmark_case_results: list[BenchmarkCaseResult]
    scorecard: Scorecard
    report_schema_version: str = REPORT_SCHEMA_VERSION
    baseline_version: str | None = None
    trace_references: list[Reference] = field(default_factory=list)
    artifact_references: list[Reference] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def validate(self) -> list[str]:
        return validate_report_payload(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalReport":
        errors = validate_report_payload(data)
        if errors:
            raise ReportValidationError(errors)
        return cls(
            report_schema_version=data.get("report_schema_version", REPORT_SCHEMA_VERSION),
            eval_run_id=data["eval_run_id"],
            created_at=data["created_at"],
            candidate_version=data["candidate_version"],
            baseline_version=data.get("baseline_version"),
            benchmark_suite_id=data["benchmark_suite_id"],
            benchmark_case_results=[BenchmarkCaseResult.from_dict(item) for item in data["benchmark_case_results"]],
            scorecard=Scorecard.from_dict(data["scorecard"]),
            trace_references=[Reference.from_dict(item) for item in data.get("trace_references", [])],
            artifact_references=[Reference.from_dict(item) for item in data.get("artifact_references", [])],
            summary=data.get("summary", {}) or {},
            metadata=data.get("metadata", {}) or {},
        )

    @classmethod
    def from_json(cls, text: str) -> "EvalReport":
        return cls.from_dict(json.loads(text))


_TOP_LEVEL_REQUIRED = (
    "eval_run_id",
    "created_at",
    "candidate_version",
    "benchmark_suite_id",
    "benchmark_case_results",
    "scorecard",
)


def validate_report_payload(data: Any) -> list[str]:
    """Validate a raw dict against the v0.2 EvalReport contract.

    Returns a list of human-readable problems; an empty list means the
    payload is structurally valid. Never raises on malformed input — the
    caller decides whether to turn the list into a `ReportValidationError`.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"report: payload must be an object, got {type(data).__name__}"]

    for name in _TOP_LEVEL_REQUIRED:
        if name not in data or data[name] is None:
            errors.append(f"report: missing required field '{name}'")

    case_results = data.get("benchmark_case_results")
    if case_results is not None:
        if not isinstance(case_results, list):
            errors.append("report: 'benchmark_case_results' must be a list")
        else:
            for index, case in enumerate(case_results):
                errors.extend(BenchmarkCaseResult.validate_payload(case, f"report.benchmark_case_results[{index}]"))

    scorecard = data.get("scorecard")
    if scorecard is not None:
        errors.extend(Scorecard.validate_payload(scorecard, "report.scorecard"))

    for ref_field in ("trace_references", "artifact_references"):
        refs = data.get(ref_field, [])
        if refs is None:
            continue
        if not isinstance(refs, list):
            errors.append(f"report: '{ref_field}' must be a list")
            continue
        for index, ref in enumerate(refs):
            errors.extend(Reference.validate_payload(ref, f"report.{ref_field}[{index}]"))

    return errors


# --------------------------------------------------------------------------
# Legacy adapter (Prompt 2 scope: additive only, no runner behavior change)
# --------------------------------------------------------------------------


def adapt_legacy_runner_report(payload: dict[str, Any]) -> EvalReport:
    """Best-effort mapping from `eval/runner.py`'s current report shape (see
    `eval/result_schema.py::BenchmarkResult` and `write_report`) into a v0.2
    `EvalReport`.

    This is a convenience for future callers wanting the new contract from
    existing data. It does not run automatically, and `eval/runner.py` is
    not changed to call it — the legacy report format keeps being written
    as-is. Legacy reports carry no per-AC evidence, trace refs, or
    token/cost accounting, so those fields are marked `not_collected`
    rather than guessed at.
    """
    results = payload.get("results", [])
    case_results: list[BenchmarkCaseResult] = []
    for result in results:
        quality = result.get("quality", {}) or {}
        metrics = result.get("metrics", {}) or {}
        status = CaseStatus.PASSED if result.get("status") == "PASS" else CaseStatus.FAILED
        ac_failed_ids = set(quality.get("ac_failed_ids", []) or [])
        story = result.get("story", {}) or {}
        ac_entries = story.get("acceptance_criteria", []) or []
        ac_results = [
            AcceptanceCriteriaResult(
                ac_id=str(entry.get("id", entry) if isinstance(entry, dict) else entry),
                description=str(entry.get("description", "")) if isinstance(entry, dict) else "",
                status=AcStatus.FAIL if str(entry.get("id", entry) if isinstance(entry, dict) else entry) in ac_failed_ids else AcStatus.PASS,
                critical=False,
                evidence=[],
            )
            for entry in ac_entries
        ]
        duration_ms = metrics.get("wall_seconds")
        trial = TrialResult(
            trial_id=str(result.get("run_id", "")),
            benchmark_id=str(result.get("name", "")),
            status=TrialStatus.PASSED if result.get("status") == "PASS" else TrialStatus.FAILED,
            started_at=str(payload.get("created_at", "")),
            completed_at=None,
            duration_ms=Metric.known(duration_ms * 1000) if isinstance(duration_ms, (int, float)) else Metric.not_collected("legacy report did not record wall-clock duration"),
            error_summary=result.get("error") or None,
            artifact_refs=[Reference(ref_id=name, uri=str(uri), kind="artifact") for name, uri in (result.get("artifacts") or {}).items()],
        )
        case_results.append(
            BenchmarkCaseResult(
                benchmark_id=str(result.get("name", "")),
                difficulty=str(result.get("name", "")),
                status=status,
                trial_results=[trial],
                aggregate_result={"weighted_score": quality.get("weighted_score")},
                acceptance_criteria_results=ac_results,
                evidence_references=[],
            )
        )

    summary = payload.get("summary", {}) or {}
    efficiency = summary.get("efficiency", {}) or {}
    reliability = summary.get("reliability", {}) or {}
    quality_summary = summary.get("quality", {}) or {}

    def _known_or_not_collected(value: Any, reason: str) -> Metric:
        return Metric.known(value) if isinstance(value, (int, float)) else Metric.not_collected(reason)

    scorecard = Scorecard(
        capability=CapabilityScore(
            ac_pass_rate=_known_or_not_collected(quality_summary.get("weighted_score"), "legacy report has no per-AC pass rate"),
            critical_ac_pass_rate=Metric.not_collected("legacy report does not distinguish critical ACs"),
            hidden_test_pass_rate=Metric.not_collected("legacy report does not separate hidden-test pass rate from weighted score"),
            full_benchmark_pass_rate=_known_or_not_collected(reliability.get("pass_rate"), "legacy report missing pass_rate"),
        ),
        reliability=ReliabilityScore(
            trial_pass_rate=_known_or_not_collected(reliability.get("pass_rate"), "legacy report missing pass_rate"),
            variance=Metric.not_collected("legacy report does not compute variance"),
            flaky_case_count=Metric.not_collected("legacy report does not track flakiness"),
            workflow_failure_rate=Metric.not_collected("legacy report does not isolate workflow-stage failures"),
        ),
        efficiency=EfficiencyScore(
            wall_clock_duration_ms=_known_or_not_collected(
                efficiency.get("wall_seconds_mean") * 1000 if isinstance(efficiency.get("wall_seconds_mean"), (int, float)) else None,
                "legacy report missing wall_seconds_mean",
            ),
            agent_invocation_count=Metric.not_collected("legacy report does not count agent invocations"),
            loop_iteration_count=Metric.not_collected("legacy report does not count loop iterations"),
            token_usage=_known_or_not_collected(efficiency.get("tokens_total_mean"), "legacy report missing tokens_total_mean"),
            estimated_cost=_known_or_not_collected(efficiency.get("cost_usd_mean"), "legacy report missing cost_usd_mean"),
        ),
        traceability=TraceabilityScore(
            trace_present=Metric.not_collected("legacy report does not emit trace references"),
            test_output_present=Metric.known(True),
            final_artifact_refs_present=Metric.known(any(result.get("artifacts") for result in results)),
            final_diff_ref_present=Metric.not_collected("legacy report does not emit a diff reference"),
            prompt_hashes_present=Metric.not_collected("legacy report does not hash prompts"),
        ),
    )

    return EvalReport(
        eval_run_id=str(payload.get("created_at", "unknown-run")),
        created_at=str(payload.get("created_at", "")),
        candidate_version=str(payload.get("sha", "unknown")),
        benchmark_suite_id=str(payload.get("runner", "unknown")),
        benchmark_case_results=case_results,
        scorecard=scorecard,
        summary=summary,
        metadata={"legacy_runner": payload.get("runner"), "legacy_model": payload.get("model")},
    )
