"""Deterministic comparison engine for two v0.2 evaluation reports.

`compare_reports(baseline, candidate)` is the single public entrypoint most
callers need. It accepts `EvalReport` instances or plain dicts, validates
both against the v0.2 contract (`eval.report_schema.validate_report_payload`),
computes AC/benchmark/scorecard/reliability/efficiency/traceability deltas
and configuration drift, and delegates final classification to
`eval.classification.classify`.

This module imports only `eval.report_schema`, `eval.comparison_schema`,
`eval.comparison_policy`, and stdlib — no server, no Opik, no LLM backend,
no telemetry package. See `tests/test_comparison_isolation.py`.

Out of scope (see docs/evaluation-comparison.md): this module does not read
`eval.benchmark_manifest` (difficulty tiers come only from the reports being
compared) and is not wired into `eval/runner.py`'s live evaluation path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from eval.classification import classify
from eval.comparison_policy import ComparisonPolicy, PolicyConfigError
from eval.comparison_schema import (
    AcDelta,
    BenchmarkDelta,
    ChangeCategory,
    Comparability,
    CompareInputError,
    ComparisonResult,
    COMPARISON_SCHEMA_VERSION,
    DeltaAvailability,
    DriftFinding,
    DriftSeverity,
    EvidenceFieldDelta,
    MetricDelta,
    ReportIdentity,
)
from eval.report_schema import EvalReport, REPORT_SCHEMA_VERSION, validate_report_payload

_TRACEABILITY_FIELDS = (
    "trace_present",
    "test_output_present",
    "final_artifact_refs_present",
    "final_diff_ref_present",
    "prompt_hashes_present",
)
_SCORECARD_CATEGORIES = ("capability", "reliability", "efficiency")


def _normalize_report(report: Any, side: str) -> dict[str, Any]:
    if isinstance(report, EvalReport):
        data = report.to_dict()
    elif isinstance(report, dict):
        data = report
    else:
        raise CompareInputError(
            f"expected an EvalReport or dict, got {type(report).__name__}", side=side
        )
    errors = validate_report_payload(data)
    if errors:
        raise CompareInputError("report failed v0.2 schema validation", side=side, errors=errors)
    version = data.get("report_schema_version", REPORT_SCHEMA_VERSION)
    if version != REPORT_SCHEMA_VERSION:
        raise CompareInputError(
            f"unsupported report_schema_version {version!r} (expected {REPORT_SCHEMA_VERSION!r})",
            side=side,
        )
    return data


def _index_cases(data: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for case in data.get("benchmark_case_results") or []:
        bid = case.get("benchmark_id")
        if bid in index:
            raise CompareInputError(f"duplicate benchmark_id '{bid}'", side=side)
        index[bid] = case
    return index


def _check_duplicate_acs(cases_index: dict[str, dict[str, Any]], side: str) -> None:
    for bid, case in cases_index.items():
        seen: set[str] = set()
        for ac in case.get("acceptance_criteria_results") or []:
            ac_id = ac.get("ac_id")
            if ac_id in seen:
                raise CompareInputError(f"duplicate ac_id '{ac_id}' in benchmark '{bid}'", side=side)
            seen.add(ac_id)


def _trial_total(data: dict[str, Any]) -> int:
    return sum(len(case.get("trial_results") or []) for case in data.get("benchmark_case_results") or [])


def _extract_identity(data: dict[str, Any], label: str | None, policy: ComparisonPolicy) -> ReportIdentity:
    meta = data.get("metadata") or {}
    metadata_subset = {key: meta.get(key) for key in policy.identity_metadata_keys if key in meta}
    return ReportIdentity(
        label=label,
        eval_run_id=data.get("eval_run_id"),
        created_at=data.get("created_at"),
        candidate_version=data.get("candidate_version"),
        baseline_version=data.get("baseline_version"),
        benchmark_suite_id=data.get("benchmark_suite_id"),
        report_schema_version=data.get("report_schema_version", REPORT_SCHEMA_VERSION),
        trial_total=_trial_total(data),
        metadata=metadata_subset,
    )


def _metric_delta(name: str, baseline_metric: dict[str, Any], candidate_metric: dict[str, Any]) -> MetricDelta:
    b_status = baseline_metric.get("status")
    c_status = candidate_metric.get("status")
    b_val = baseline_metric.get("value")
    c_val = candidate_metric.get("value")

    if b_status == "known" and c_status == "known":
        if isinstance(b_val, bool) or isinstance(c_val, bool):
            return MetricDelta(
                name=name,
                baseline_status=b_status,
                baseline_value=b_val,
                candidate_status=c_status,
                candidate_value=c_val,
                absolute_delta=None,
                relative_delta=None,
                availability=DeltaAvailability.COMPUTED,
                reason="boolean metric; see evidence_deltas for presence/loss semantics",
            )
        absolute = c_val - b_val
        if b_val == 0:
            relative = None
            reason: str | None = "relative delta undefined: baseline value is 0 (absolute delta still computed)"
        else:
            relative = absolute / b_val
            reason = None
        return MetricDelta(
            name=name,
            baseline_status=b_status,
            baseline_value=b_val,
            candidate_status=c_status,
            candidate_value=c_val,
            absolute_delta=absolute,
            relative_delta=relative,
            availability=DeltaAvailability.COMPUTED,
            reason=reason,
        )
    if c_status == "known" and b_status != "known":
        reason = f"baseline value unavailable (status={b_status})"
    elif b_status == "known" and c_status != "known":
        reason = f"candidate value unavailable (status={c_status})"
    else:
        reason = f"both sides unavailable (baseline status={b_status}, candidate status={c_status})"
    return MetricDelta(
        name=name,
        baseline_status=b_status,
        baseline_value=b_val,
        candidate_status=c_status,
        candidate_value=c_val,
        absolute_delta=None,
        relative_delta=None,
        availability=DeltaAvailability.UNAVAILABLE,
        reason=reason,
    )


def _scorecard_deltas(baseline_data: dict[str, Any], candidate_data: dict[str, Any]) -> dict[str, dict[str, MetricDelta]]:
    baseline_scorecard = baseline_data.get("scorecard") or {}
    candidate_scorecard = candidate_data.get("scorecard") or {}
    result: dict[str, dict[str, MetricDelta]] = {}
    for category in _SCORECARD_CATEGORIES:
        b_section = baseline_scorecard.get(category) or {}
        c_section = candidate_scorecard.get(category) or {}
        names = sorted(set(b_section) | set(c_section))
        result[category] = {name: _metric_delta(name, b_section.get(name, {}), c_section.get(name, {})) for name in names}
    return result


def _evidence_deltas(baseline_data: dict[str, Any], candidate_data: dict[str, Any], policy: ComparisonPolicy) -> list[EvidenceFieldDelta]:
    tiers: dict[str, str] = {}
    for name in policy.required_evidence_fields:
        tiers[name] = "required"
    for name in policy.conditional_evidence_fields:
        tiers[name] = "conditional"
    for name in policy.optional_evidence_fields:
        tiers[name] = "optional"

    b_trace = (baseline_data.get("scorecard") or {}).get("traceability") or {}
    c_trace = (candidate_data.get("scorecard") or {}).get("traceability") or {}
    deltas = []
    for name in _TRACEABILITY_FIELDS:
        b = b_trace.get(name, {})
        c = c_trace.get(name, {})
        b_status, b_val = b.get("status"), b.get("value")
        c_status, c_val = c.get("status"), c.get("value")
        regressed = b_status == "known" and b_val is True and c_status == "known" and c_val is False
        degraded = b_status == "known" and c_status != "known"
        gained = (b_status != "known" or b_val is False) and c_status == "known" and c_val is True
        deltas.append(
            EvidenceFieldDelta(
                field=name,
                requirement=tiers.get(name, "optional"),
                baseline_status=b_status,
                baseline_value=b_val,
                candidate_status=c_status,
                candidate_value=c_val,
                regressed=regressed,
                degraded=degraded,
                gained=gained,
            )
        )
    return deltas


def _ac_pass_rate(case: dict[str, Any]) -> float | None:
    acs = case.get("acceptance_criteria_results") or []
    known = [ac for ac in acs if ac.get("status") in ("pass", "fail")]
    if not known:
        return None
    passed = sum(1 for ac in known if ac.get("status") == "pass")
    return passed / len(known)


def _outcome(status: str | None) -> str:
    if status == "pass":
        return "pass"
    if status == "fail":
        return "fail"
    return "indeterminate"


def _ac_category(baseline_status: str | None, candidate_status: str | None) -> tuple[ChangeCategory, str | None]:
    if baseline_status == candidate_status:
        return ChangeCategory.UNCHANGED, None
    b_outcome, c_outcome = _outcome(baseline_status), _outcome(candidate_status)
    if b_outcome == "indeterminate" or c_outcome == "indeterminate":
        return (
            ChangeCategory.INCOMPARABLE,
            f"status changed from '{baseline_status}' to '{candidate_status}' but is not a pass/fail transition",
        )
    if b_outcome == "fail" and c_outcome == "pass":
        return ChangeCategory.IMPROVED, None
    if b_outcome == "pass" and c_outcome == "fail":
        return ChangeCategory.REGRESSED, None
    return ChangeCategory.UNCHANGED, None


def _build_ac_deltas(
    baseline_cases: dict[str, dict[str, Any]], candidate_cases: dict[str, dict[str, Any]]
) -> list[AcDelta]:
    baseline_index: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for bid, case in baseline_cases.items():
        for ac in case.get("acceptance_criteria_results") or []:
            baseline_index[(bid, ac.get("ac_id"))] = (case, ac)
    candidate_index: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for bid, case in candidate_cases.items():
        for ac in case.get("acceptance_criteria_results") or []:
            candidate_index[(bid, ac.get("ac_id"))] = (case, ac)

    deltas: list[AcDelta] = []
    for key in sorted(set(baseline_index) | set(candidate_index)):
        bid, ac_id = key
        b_entry = baseline_index.get(key)
        c_entry = candidate_index.get(key)
        if b_entry is None:
            c_case, c_ac = c_entry  # type: ignore[misc]
            deltas.append(
                AcDelta(
                    benchmark_id=bid,
                    ac_id=ac_id,
                    critical=bool(c_ac.get("critical", False)),
                    baseline_status=None,
                    candidate_status=c_ac.get("status"),
                    baseline_difficulty=None,
                    candidate_difficulty=c_case.get("difficulty"),
                    category=ChangeCategory.ADDED,
                )
            )
            continue
        if c_entry is None:
            b_case, b_ac = b_entry
            deltas.append(
                AcDelta(
                    benchmark_id=bid,
                    ac_id=ac_id,
                    critical=bool(b_ac.get("critical", False)),
                    baseline_status=b_ac.get("status"),
                    candidate_status=None,
                    baseline_difficulty=b_case.get("difficulty"),
                    candidate_difficulty=None,
                    category=ChangeCategory.REMOVED,
                )
            )
            continue
        b_case, b_ac = b_entry
        c_case, c_ac = c_entry
        category, reason = _ac_category(b_ac.get("status"), c_ac.get("status"))
        deltas.append(
            AcDelta(
                benchmark_id=bid,
                ac_id=ac_id,
                critical=bool(c_ac.get("critical", b_ac.get("critical", False))),
                baseline_status=b_ac.get("status"),
                candidate_status=c_ac.get("status"),
                baseline_difficulty=b_case.get("difficulty"),
                candidate_difficulty=c_case.get("difficulty"),
                category=category,
                incomparable_reason=reason,
            )
        )
    return deltas


def _benchmark_category(baseline_status: str | None, candidate_status: str | None) -> tuple[ChangeCategory, str | None]:
    def outcome(status: str | None) -> str:
        if status == "passed":
            return "pass"
        if status in ("failed", "error"):
            return "fail"
        return "indeterminate"

    if baseline_status == candidate_status:
        return ChangeCategory.UNCHANGED, None
    b_outcome, c_outcome = outcome(baseline_status), outcome(candidate_status)
    if b_outcome == "indeterminate" or c_outcome == "indeterminate":
        return (
            ChangeCategory.INCOMPARABLE,
            f"status changed from '{baseline_status}' to '{candidate_status}' but is not a passed/failed transition",
        )
    if b_outcome == "fail" and c_outcome == "pass":
        return ChangeCategory.IMPROVED, None
    if b_outcome == "pass" and c_outcome == "fail":
        return ChangeCategory.REGRESSED, None
    return ChangeCategory.UNCHANGED, None


def _build_benchmark_deltas(
    baseline_cases: dict[str, dict[str, Any]], candidate_cases: dict[str, dict[str, Any]], policy: ComparisonPolicy
) -> list[BenchmarkDelta]:
    deltas: list[BenchmarkDelta] = []
    for bid in sorted(set(baseline_cases) | set(candidate_cases)):
        b_case = baseline_cases.get(bid)
        c_case = candidate_cases.get(bid)
        if b_case is None:
            deltas.append(
                BenchmarkDelta(
                    benchmark_id=bid,
                    baseline_difficulty=None,
                    candidate_difficulty=c_case.get("difficulty"),
                    baseline_status=None,
                    candidate_status=c_case.get("status"),
                    baseline_ac_pass_rate=None,
                    candidate_ac_pass_rate=_ac_pass_rate(c_case),
                    baseline_trial_count=None,
                    candidate_trial_count=len(c_case.get("trial_results") or []),
                    trial_count_comparable=False,
                    category=ChangeCategory.ADDED,
                    newly_failing=False,
                    resolved_failure=False,
                    easy_regression=False,
                )
            )
            continue
        if c_case is None:
            deltas.append(
                BenchmarkDelta(
                    benchmark_id=bid,
                    baseline_difficulty=b_case.get("difficulty"),
                    candidate_difficulty=None,
                    baseline_status=b_case.get("status"),
                    candidate_status=None,
                    baseline_ac_pass_rate=_ac_pass_rate(b_case),
                    candidate_ac_pass_rate=None,
                    baseline_trial_count=len(b_case.get("trial_results") or []),
                    candidate_trial_count=None,
                    trial_count_comparable=False,
                    category=ChangeCategory.REMOVED,
                    newly_failing=False,
                    resolved_failure=False,
                    easy_regression=False,
                )
            )
            continue

        b_trials = len(b_case.get("trial_results") or [])
        c_trials = len(c_case.get("trial_results") or [])
        if min(b_trials, c_trials) > 0:
            trial_comparable = (
                b_trials >= policy.min_trials
                and c_trials >= policy.min_trials
                and max(b_trials, c_trials) / min(b_trials, c_trials) <= policy.max_trial_count_ratio
            )
        else:
            trial_comparable = False

        category, reason = _benchmark_category(b_case.get("status"), c_case.get("status"))
        difficulty_for_tier = c_case.get("difficulty") or b_case.get("difficulty")
        easy_regression = category == ChangeCategory.REGRESSED and difficulty_for_tier == "easy"
        deltas.append(
            BenchmarkDelta(
                benchmark_id=bid,
                baseline_difficulty=b_case.get("difficulty"),
                candidate_difficulty=c_case.get("difficulty"),
                baseline_status=b_case.get("status"),
                candidate_status=c_case.get("status"),
                baseline_ac_pass_rate=_ac_pass_rate(b_case),
                candidate_ac_pass_rate=_ac_pass_rate(c_case),
                baseline_trial_count=b_trials,
                candidate_trial_count=c_trials,
                trial_count_comparable=trial_comparable,
                category=category,
                newly_failing=category == ChangeCategory.REGRESSED,
                resolved_failure=category == ChangeCategory.IMPROVED,
                easy_regression=easy_regression,
                incomparable_reason=reason,
            )
        )
    return deltas


def _detect_drift(baseline_data: dict[str, Any], candidate_data: dict[str, Any], policy: ComparisonPolicy) -> list[DriftFinding]:
    drift: list[DriftFinding] = []
    b_suite = baseline_data.get("benchmark_suite_id")
    c_suite = candidate_data.get("benchmark_suite_id")
    if b_suite != c_suite:
        drift.append(
            DriftFinding(
                field="benchmark_suite_id",
                baseline_value=b_suite,
                candidate_value=c_suite,
                severity=DriftSeverity.INCOMPATIBLE,
                explanation="baseline and candidate report different benchmark_suite_id values; AC/benchmark identity cannot be trusted across suites",
            )
        )
    b_meta = baseline_data.get("metadata") or {}
    c_meta = candidate_data.get("metadata") or {}
    for key in policy.identity_metadata_keys:
        if key not in b_meta and key not in c_meta:
            continue
        b_val = b_meta.get(key)
        c_val = c_meta.get(key)
        if b_val != c_val:
            severity = DriftSeverity.MATERIAL if key in policy.material_drift_metadata_keys else DriftSeverity.INFORMATIONAL
            drift.append(
                DriftFinding(
                    field=f"metadata.{key}",
                    baseline_value=b_val,
                    candidate_value=c_val,
                    severity=severity,
                    explanation=f"metadata.{key} differs between baseline and candidate",
                )
            )
    return drift


def _determine_comparability(
    baseline_identity: ReportIdentity,
    candidate_identity: ReportIdentity,
    drift: list[DriftFinding],
    evidence_deltas: list[EvidenceFieldDelta],
    benchmark_deltas: list[BenchmarkDelta],
) -> tuple[Comparability, list[str]]:
    reasons: list[str] = []
    level = Comparability.COMPARABLE

    if any(d.severity == DriftSeverity.INCOMPATIBLE for d in drift):
        level = Comparability.INCOMPARABLE
        reasons.append("benchmark_suite_id differs between baseline and candidate; results are not comparable")

    if baseline_identity.trial_total == 0 or candidate_identity.trial_total == 0:
        level = Comparability.INCOMPARABLE
        side = "baseline" if baseline_identity.trial_total == 0 else "candidate"
        reasons.append(f"{side} report has zero recorded trials across all benchmark cases")

    required_degraded = [e for e in evidence_deltas if e.requirement == "required" and e.degraded]
    if required_degraded and level == Comparability.COMPARABLE:
        level = Comparability.DEGRADED
        for evidence in required_degraded:
            reasons.append(
                f"required evidence field '{evidence.field}' became unavailable on the candidate "
                f"(was known on baseline)"
            )

    if benchmark_deltas and level == Comparability.COMPARABLE:
        shared = [d for d in benchmark_deltas if d.category not in (ChangeCategory.ADDED, ChangeCategory.REMOVED)]
        if not shared:
            level = Comparability.INCOMPARABLE
            reasons.append("baseline and candidate share no benchmark_id in common")

    return level, reasons


def compare_reports(
    baseline: EvalReport | dict[str, Any],
    candidate: EvalReport | dict[str, Any],
    *,
    policy: ComparisonPolicy | None = None,
    baseline_label: str | None = None,
    candidate_label: str | None = None,
) -> ComparisonResult:
    """Compare a baseline and candidate v0.2 evaluation report.

    Raises `CompareInputError` (tagged with `.side` = "baseline" / "candidate"
    / "policy") for schema failures, unsupported schema versions, duplicate
    benchmark/AC ids, or invalid policy configuration. Never mutates the
    inputs. Deterministic: identical inputs always produce an identical
    `ComparisonResult`.
    """
    policy = policy or ComparisonPolicy()
    try:
        policy.validate()
    except PolicyConfigError as exc:
        raise CompareInputError(str(exc), side="policy") from exc

    baseline_data = _normalize_report(baseline, "baseline")
    candidate_data = _normalize_report(candidate, "candidate")

    baseline_cases = _index_cases(baseline_data, "baseline")
    candidate_cases = _index_cases(candidate_data, "candidate")
    _check_duplicate_acs(baseline_cases, "baseline")
    _check_duplicate_acs(candidate_cases, "candidate")

    baseline_identity = _extract_identity(baseline_data, baseline_label, policy)
    candidate_identity = _extract_identity(candidate_data, candidate_label, policy)

    ac_deltas = _build_ac_deltas(baseline_cases, candidate_cases)
    benchmark_deltas = _build_benchmark_deltas(baseline_cases, candidate_cases, policy)
    scorecard_deltas = _scorecard_deltas(baseline_data, candidate_data)
    evidence_deltas = _evidence_deltas(baseline_data, candidate_data, policy)
    drift = _detect_drift(baseline_data, candidate_data, policy)
    comparability, comparability_reasons = _determine_comparability(
        baseline_identity, candidate_identity, drift, evidence_deltas, benchmark_deltas
    )

    classification, reasons = classify(
        comparability=comparability,
        drift=drift,
        ac_deltas=ac_deltas,
        benchmark_deltas=benchmark_deltas,
        scorecard_deltas=scorecard_deltas,
        evidence_deltas=evidence_deltas,
        policy=policy,
    )

    def _by(deltas: list[AcDelta], category: ChangeCategory) -> list[AcDelta]:
        return [d for d in deltas if d.category == category]

    def _by_b(deltas: list[BenchmarkDelta], category: ChangeCategory) -> list[BenchmarkDelta]:
        return [d for d in deltas if d.category == category]

    warnings: list[str] = []
    if comparability != Comparability.COMPARABLE:
        warnings.extend(comparability_reasons)

    return ComparisonResult(
        comparison_schema_version=COMPARISON_SCHEMA_VERSION,
        baseline_identity=baseline_identity,
        candidate_identity=candidate_identity,
        comparability=comparability,
        comparability_reasons=comparability_reasons,
        drift=drift,
        ac_deltas=ac_deltas,
        improved_acs=_by(ac_deltas, ChangeCategory.IMPROVED),
        regressed_acs=_by(ac_deltas, ChangeCategory.REGRESSED),
        unchanged_acs=_by(ac_deltas, ChangeCategory.UNCHANGED),
        added_acs=_by(ac_deltas, ChangeCategory.ADDED),
        removed_acs=_by(ac_deltas, ChangeCategory.REMOVED),
        incomparable_acs=_by(ac_deltas, ChangeCategory.INCOMPARABLE),
        critical_ac_regressions=[d for d in ac_deltas if d.critical and d.category == ChangeCategory.REGRESSED],
        critical_ac_improvements=[d for d in ac_deltas if d.critical and d.category == ChangeCategory.IMPROVED],
        benchmark_deltas=benchmark_deltas,
        improved_benchmarks=_by_b(benchmark_deltas, ChangeCategory.IMPROVED),
        regressed_benchmarks=_by_b(benchmark_deltas, ChangeCategory.REGRESSED),
        unchanged_benchmarks=_by_b(benchmark_deltas, ChangeCategory.UNCHANGED),
        added_benchmarks=_by_b(benchmark_deltas, ChangeCategory.ADDED),
        removed_benchmarks=_by_b(benchmark_deltas, ChangeCategory.REMOVED),
        incomparable_benchmarks=_by_b(benchmark_deltas, ChangeCategory.INCOMPARABLE),
        easy_regressions=[d for d in benchmark_deltas if d.easy_regression],
        new_failures=[d for d in benchmark_deltas if d.newly_failing],
        resolved_failures=[d for d in benchmark_deltas if d.resolved_failure],
        scorecard_deltas=scorecard_deltas,
        evidence_deltas=evidence_deltas,
        evidence_regressions=[e for e in evidence_deltas if e.regressed or (e.requirement != "optional" and e.degraded)],
        classification=classification,
        reasons=reasons,
        policy=policy.to_dict(),
        warnings=warnings,
    )


def load_and_compare(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    policy: ComparisonPolicy | None = None,
) -> ComparisonResult:
    """Load two v0.2 reports from disk and compare them.

    Reuses the repo's existing conventions rather than inventing new I/O:
    `Path.read_text(encoding="utf-8")` (the idiom used throughout
    `eval/runner.py` and `eval/benchmark_manifest.py`) feeding
    `EvalReport.from_json` (the deserializer already defined on `EvalReport`
    in `eval/report_schema.py`). This function adds no new loading
    convention of its own.
    """
    baseline_path = Path(baseline_path)
    candidate_path = Path(candidate_path)
    try:
        baseline_report = EvalReport.from_json(baseline_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - re-tag as a labeled CompareInputError
        raise CompareInputError(f"failed to load baseline report from {baseline_path}: {exc}", side="baseline") from exc
    try:
        candidate_report = EvalReport.from_json(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CompareInputError(f"failed to load candidate report from {candidate_path}: {exc}", side="candidate") from exc
    return compare_reports(
        baseline_report,
        candidate_report,
        policy=policy,
        baseline_label=str(baseline_path),
        candidate_label=str(candidate_path),
    )
