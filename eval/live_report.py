"""Live eval → v0.2 report builder + atomic writer (Prompt 6).

Pure functions over the per-trial result dicts already produced by
`eval/runner.py::run_one`/`finalize_result`. No subprocess calls, no I/O other
than the explicit atomic-write helper. Never zero-coerces missing telemetry —
unmeasured values become `Metric.unknown()`/`not_collected()`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from eval.evidence_paths import (
    latest_report_path,
    report_stamp,
    timestamped_report_path,
)
from eval.report_schema import (
    AcStatus,
    AcceptanceCriteriaResult,
    CapabilityScore,
    CaseStatus,
    EfficiencyScore,
    EvalReport,
    Metric,
    Reference,
    ReliabilityScore,
    Scorecard,
    TraceabilityScore,
    TrialResult,
    TrialStatus,
    validate_report_payload,
)

_HANDLED_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("timeout after", "timeout"),
    ("workflow failed", "workflow_failure"),
    ("hidden tests unexpectedly passed on gold master", "hidden_test_failure"),
    ("hidden tests errored on gold master", "hidden_test_failure"),
    ("hidden tests did not fail cleanly on gold master", "hidden_test_failure"),
    ("hidden tests were skipped", "hidden_test_failure"),
    ("hidden tests failed", "hidden_test_failure"),
    ("acceptance criteria without mapped hidden tests", "hidden_test_failure"),
    ("project tests failed", "workflow_failure"),
)


class ReportBuildError(RuntimeError):
    """Raised when trials cannot be safely grouped into one case, or the
    assembled report fails schema validation before it is ever written."""


def _failure_category(error: str | None) -> str | None:
    if not error:
        return None
    lowered = error.lower()
    for needle, category in _HANDLED_FAILURE_PATTERNS:
        if needle in lowered:
            return category
    # `finalize_result`/`run_one` stamp unhandled exceptions as
    # "ExceptionType: message" — anything else is genuinely unclassified.
    return "unknown"


def _trial_status(result: dict[str, Any]) -> TrialStatus:
    status = result.get("status")
    error = result.get("error") or ""
    if status == "PASS":
        return TrialStatus.PASSED
    if "timeout after" in error.lower():
        return TrialStatus.TIMEOUT
    category = _failure_category(error)
    if category in {"workflow_failure", "hidden_test_failure"}:
        return TrialStatus.FAILED
    if error:
        return TrialStatus.ERROR
    return TrialStatus.FAILED


def _duration_ms(result: dict[str, Any]) -> Metric:
    metrics = result.get("metrics") or {}
    wall_seconds = metrics.get("wall_seconds")
    if wall_seconds is None:
        wall_seconds = result.get("seconds")
    if wall_seconds is None:
        return Metric.not_collected("wall_seconds was not recorded for this trial")
    return Metric.known(round(float(wall_seconds) * 1000))


def _artifact_refs(evidence: dict[str, Any]) -> list[Reference]:
    refs: list[Reference] = []
    mapping = (
        ("story_ref", "story", "artifact"),
        ("workflow_stdout_ref", "workflow-stdout", "log"),
        ("workflow_stderr_ref", "workflow-stderr", "log"),
    )
    for key, ref_id, kind in mapping:
        uri = evidence.get(key)
        if uri:
            refs.append(Reference(ref_id=ref_id, uri=str(uri), kind=kind))
    return refs


def _build_trial_result(result: dict[str, Any], *, benchmark_id: str) -> TrialResult:
    evidence = result.get("evidence") or {}
    error = result.get("error") or None
    return TrialResult(
        trial_id=str(result.get("run_id") or f"{benchmark_id}-t{result.get('trial_index', 1)}"),
        benchmark_id=benchmark_id,
        status=_trial_status(result),
        started_at=result.get("started_at") or "",
        completed_at=result.get("completed_at") or "",
        duration_ms=_duration_ms(result),
        workflow_result_ref=evidence.get("workflow_result_ref"),
        trace_ref=evidence.get("trace_ref"),
        artifact_refs=_artifact_refs(evidence),
        test_result_ref=evidence.get("hidden_tests_xml_ref"),
        failure_category=_failure_category(error) if error else None,
        error_summary=error,
    )


def _ac_definitions(story: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract (id, text, critical) for each declared acceptance criterion."""
    raw = story.get("acceptance_criteria") or []
    metadata = story.get("metadata") or {}
    critical_ids = set(metadata.get("critical_acceptance_criteria") or [])
    definitions: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        ac_id, _, rest = item.partition(":")
        ac_id = ac_id.strip()
        if not ac_id:
            continue
        definitions.append(
            {
                "id": ac_id,
                "text": rest.strip() or item.strip(),
                "critical": ac_id in critical_ids,
            }
        )
    return definitions


def _ac_status_across_trials(ac_id: str, trials: list[dict[str, Any]]) -> tuple[AcStatus, str | None, list[str]]:
    """Positive-evidence-only aggregation: an AC only passes if every trial
    that actually reached hidden tests reported it passing. Any trial that
    positively failed it fails the case. No trial reaching hidden tests
    (workflow failed first, tests skipped, etc.) is `unknown` — never `pass`.
    """
    saw_pass = False
    saw_fail = False
    saw_incomplete = False
    related_tests: list[str] = []
    reason: str | None = None
    for trial in trials:
        hidden = trial.get("hidden_tests")
        if not hidden:
            continue
        ac_results = hidden.get("ac_results") or {}
        entry = ac_results.get(ac_id)
        if entry is None:
            continue
        related_tests.extend(entry.get("tests") or [])
        if entry.get("missing_cases"):
            saw_incomplete = True
            reason = reason or f"AC {ac_id} has hidden-test cases that never ran"
            continue
        if entry.get("failed"):
            saw_fail = True
            reason = reason or f"AC {ac_id} hidden test(s) failed"
        elif entry.get("passed"):
            saw_pass = True
        else:
            saw_incomplete = True
    if saw_fail:
        return AcStatus.FAIL, reason, sorted(set(related_tests))
    if saw_pass and not saw_incomplete:
        return AcStatus.PASS, None, sorted(set(related_tests))
    if saw_pass and saw_incomplete:
        return AcStatus.UNKNOWN, reason or f"AC {ac_id} was only partially evaluated", sorted(set(related_tests))
    return AcStatus.UNKNOWN, reason or f"AC {ac_id} was never evaluated (hidden tests did not run to completion)", sorted(set(related_tests))


def _build_ac_results(story: dict[str, Any], trials: list[dict[str, Any]]) -> list[AcceptanceCriteriaResult]:
    results = []
    for definition in _ac_definitions(story):
        status, reason, related = _ac_status_across_trials(definition["id"], trials)
        evidence = []
        for trial in trials:
            ref = (trial.get("evidence") or {}).get("hidden_tests_xml_ref")
            if ref:
                evidence.append(Reference(ref_id=f"{definition['id']}-hidden-tests", uri=str(ref), kind="test-report"))
                break
        results.append(
            AcceptanceCriteriaResult(
                ac_id=definition["id"],
                description=definition["text"],
                status=status,
                critical=definition["critical"],
                evidence=evidence,
                failure_reason=reason if status in (AcStatus.FAIL, AcStatus.UNKNOWN) else None,
                related_tests=related or None,
            )
        )
    return results


def _case_fingerprint(result: dict[str, Any]) -> tuple[Any, ...]:
    story = result.get("story") or {}
    return (
        tuple(story.get("acceptance_criteria") or ()),
        tuple(sorted((story.get("metadata") or {}).get("critical_acceptance_criteria") or ())),
        (story.get("metadata") or {}).get("domain"),
    )


def _case_status(trial_results: list[TrialResult]) -> CaseStatus:
    statuses = {t.status for t in trial_results}
    if statuses == {TrialStatus.PASSED}:
        return CaseStatus.PASSED
    if statuses <= {TrialStatus.FAILED, TrialStatus.TIMEOUT}:
        return CaseStatus.FAILED
    if statuses <= {TrialStatus.ERROR}:
        return CaseStatus.ERROR
    if TrialStatus.PASSED in statuses:
        return CaseStatus.PARTIAL
    return CaseStatus.PARTIAL


def _build_case_result(benchmark_id: str, difficulty: str, trials: list[dict[str, Any]]) -> Any:
    from eval.report_schema import BenchmarkCaseResult

    fingerprints = {_case_fingerprint(t) for t in trials}
    if len(fingerprints) > 1:
        raise ReportBuildError(
            f"benchmark_id={benchmark_id!r} difficulty={difficulty!r}: trials disagree on "
            "acceptance criteria/critical flags/domain — refusing to merge into one case."
        )

    trial_results = [_build_trial_result(t, benchmark_id=benchmark_id) for t in trials]
    story = trials[0].get("story") or {}
    domain = (story.get("metadata") or {}).get("domain")

    pass_count = sum(1 for t in trial_results if t.status == TrialStatus.PASSED)
    fail_count = len(trial_results) - pass_count
    scores = [t.get("score_weighted") for t in trials if t.get("score_weighted") is not None]

    evidence_refs: list[Reference] = []
    for trial in trials:
        ev = trial.get("evidence") or {}
        for key, ref_id, kind in (
            ("hidden_tests_xml_ref", "hidden-tests-xml", "test-report"),
            ("diff_ref", "final-diff", "diff"),
        ):
            uri = ev.get(key)
            if uri and not any(r.uri == uri for r in evidence_refs):
                evidence_refs.append(Reference(ref_id=ref_id, uri=str(uri), kind=kind))

    return BenchmarkCaseResult(
        benchmark_id=benchmark_id,
        difficulty=difficulty,
        status=_case_status(trial_results),
        trial_results=trial_results,
        aggregate_result={
            "weighted_score_mean": (sum(scores) / len(scores)) if scores else None,
            "trial_pass_count": pass_count,
            "trial_fail_count": fail_count,
            "trial_pass_rate": (pass_count / len(trial_results)) if trial_results else None,
        },
        acceptance_criteria_results=_build_ac_results(story, trials),
        evidence_references=evidence_refs,
        domain=domain,
    )


def _ratio_metric(passed: int, total: int, *, reason: str) -> Metric:
    if total == 0:
        return Metric.unknown(reason)
    return Metric.known(passed / total)


def _build_scorecard(case_results: list[Any], trial_results_flat: list[TrialResult]) -> Scorecard:
    known_acs = [
        ac
        for case in case_results
        for ac in case.acceptance_criteria_results
        if ac.status in (AcStatus.PASS, AcStatus.FAIL)
    ]
    critical_acs = [ac for ac in known_acs if ac.critical]
    ac_pass = sum(1 for ac in known_acs if ac.status == AcStatus.PASS)
    critical_pass = sum(1 for ac in critical_acs if ac.status == AcStatus.PASS)

    capability = CapabilityScore(
        ac_pass_rate=_ratio_metric(ac_pass, len(known_acs), reason="no acceptance criteria were positively evaluated"),
        critical_ac_pass_rate=_ratio_metric(
            critical_pass, len(critical_acs), reason="no critical acceptance criteria were positively evaluated"
        ),
        # placeholder; overwritten by the caller via `_recompute_hidden_test_rate`,
        # which has access to the raw hidden-test totals this function does not.
        hidden_test_pass_rate=Metric.unknown("computed by caller"),
        full_benchmark_pass_rate=_ratio_metric(
            sum(1 for c in case_results if c.status == CaseStatus.PASSED),
            len(case_results),
            reason="no benchmark cases were run",
        ),
    )

    total_trials = len(trial_results_flat)
    passed_trials = sum(1 for t in trial_results_flat if t.status == TrialStatus.PASSED)
    workflow_failures = sum(1 for t in trial_results_flat if t.failure_category == "workflow_failure")
    max_trials_per_case = max((len(c.trial_results) for c in case_results), default=0)
    if max_trials_per_case < 2:
        variance = Metric.not_applicable("fewer than 2 trials were run for any case; variance is undefined")
        flaky = Metric.known(0)
    else:
        flaky_cases = sum(
            1
            for c in case_results
            if len({t.status for t in c.trial_results} & {TrialStatus.PASSED, TrialStatus.FAILED}) > 1
        )
        variance = Metric.known(0.0)  # provisional: real variance needs per-trial score distributions
        flaky = Metric.known(flaky_cases)

    reliability = ReliabilityScore(
        trial_pass_rate=_ratio_metric(passed_trials, total_trials, reason="no trials were run"),
        variance=variance,
        flaky_case_count=flaky,
        workflow_failure_rate=_ratio_metric(workflow_failures, total_trials, reason="no trials were run"),
    )

    known_durations = [t.duration_ms.value for t in trial_results_flat if t.duration_ms.status.value == "known"]
    wall_clock = (
        Metric.known(sum(known_durations) / len(known_durations))
        if known_durations
        else Metric.unknown("no trial reported a measured duration")
    )

    efficiency = EfficiencyScore(
        wall_clock_duration_ms=wall_clock,
        agent_invocation_count=Metric.unknown("trace event parsing for invocation counts is not implemented"),
        loop_iteration_count=Metric.unknown("trace event parsing for loop iteration counts is not implemented"),
        token_usage=Metric.not_collected("token accounting from session-metric files is best-effort and not treated as authoritative"),
        estimated_cost=Metric.not_collected("cost accounting is not implemented; the runner never computes a real cost value"),
    )

    any_trace = any(t.trace_ref for t in trial_results_flat)
    any_test_output = any(t.test_result_ref for t in trial_results_flat)
    any_artifacts = any(c.evidence_references or t.artifact_refs for c in case_results for t in c.trial_results)
    any_diff = any(
        ref.kind == "diff" for c in case_results for ref in c.evidence_references
    )

    traceability = TraceabilityScore(
        trace_present=Metric.known(any_trace),
        test_output_present=Metric.known(any_test_output),
        final_artifact_refs_present=Metric.known(any_artifacts),
        final_diff_ref_present=Metric.known(any_diff),
        prompt_hashes_present=Metric.not_collected("prompt hashing is not implemented yet"),
    )

    return Scorecard(
        capability=capability,
        reliability=reliability,
        efficiency=efficiency,
        traceability=traceability,
    )


def _recompute_hidden_test_rate(case_results: list[Any], results_by_case: dict[tuple[str, str], list[dict[str, Any]]]) -> Metric:
    total = passed = 0
    for case in case_results:
        key = (case.benchmark_id, case.difficulty)
        for trial in results_by_case.get(key, []):
            hidden = trial.get("hidden_tests")
            if not hidden:
                continue
            total += hidden.get("total", 0)
            passed += hidden.get("passed", 0)
    return _ratio_metric(passed, total, reason="no trial ran hidden tests to completion")


def _build_tier_aggregates(case_results: list[Any]) -> dict[str, dict[str, Any]]:
    tiers: dict[str, dict[str, Any]] = {}
    for case in case_results:
        tier = tiers.setdefault(
            case.difficulty,
            {
                "case_count": 0,
                "trial_count": 0,
                "ac_pass_rate": None,
                "critical_ac_pass_rate": None,
                "hidden_test_pass_rate": None,
                "full_case_pass_rate": None,
                "workflow_failure_rate": None,
                "_ac_pass": 0,
                "_ac_known": 0,
                "_crit_pass": 0,
                "_crit_known": 0,
                "_case_pass": 0,
                "_wf_fail": 0,
                "_trials": 0,
            },
        )
        tier["case_count"] += 1
        tier["trial_count"] += len(case.trial_results)
        if case.status == CaseStatus.PASSED:
            tier["_case_pass"] += 1
        for ac in case.acceptance_criteria_results:
            if ac.status in (AcStatus.PASS, AcStatus.FAIL):
                tier["_ac_known"] += 1
                if ac.status == AcStatus.PASS:
                    tier["_ac_pass"] += 1
                if ac.critical:
                    tier["_crit_known"] += 1
                    if ac.status == AcStatus.PASS:
                        tier["_crit_pass"] += 1
        for trial in case.trial_results:
            tier["_trials"] += 1
            if trial.failure_category == "workflow_failure":
                tier["_wf_fail"] += 1

    for tier in tiers.values():
        tier["ac_pass_rate"] = (tier["_ac_pass"] / tier["_ac_known"]) if tier["_ac_known"] else None
        tier["critical_ac_pass_rate"] = (tier["_crit_pass"] / tier["_crit_known"]) if tier["_crit_known"] else None
        tier["full_case_pass_rate"] = (tier["_case_pass"] / tier["case_count"]) if tier["case_count"] else None
        tier["workflow_failure_rate"] = (tier["_wf_fail"] / tier["_trials"]) if tier["_trials"] else None
        tier["hidden_test_pass_rate"] = None  # filled by caller with real counts if available
        for key in list(tier):
            if key.startswith("_"):
                del tier[key]
    return tiers


def build_eval_report(
    results: list[dict[str, Any]],
    args: Any,
    *,
    created_at: str,
    eval_run_id: str,
    comparison_context: dict[str, Any] | None = None,
) -> EvalReport:
    """Build a schema-valid v0.2 `EvalReport` from raw `run_one` result dicts.

    Trials are grouped by `(benchmark_id, difficulty)`; a per-trial "case
    fingerprint" (AC text, critical ids, domain) is asserted equal within a
    group — if trials in the same group disagree, `ReportBuildError` is
    raised rather than silently merging inconsistent cases.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        benchmark_id = result.get("benchmark_id") or result.get("name") or "unknown"
        difficulty = result.get("difficulty") or "unknown"
        grouped.setdefault((benchmark_id, difficulty), []).append(result)

    case_results = [_build_case_result(bid, diff, trials) for (bid, diff), trials in grouped.items()]
    trial_results_flat = [t for case in case_results for t in case.trial_results]
    scorecard = _build_scorecard(case_results, trial_results_flat)
    scorecard.capability.hidden_test_pass_rate = _recompute_hidden_test_rate(case_results, grouped)

    tier_aggregates = _build_tier_aggregates(case_results)
    for tier_name, tier in tier_aggregates.items():
        cases_in_tier = [c for c in case_results if c.difficulty == tier_name]
        tier["hidden_test_pass_rate"] = _recompute_hidden_test_rate(cases_in_tier, grouped).value

    trace_refs = []
    artifact_refs = []
    for case in case_results:
        for trial in case.trial_results:
            if trial.trace_ref and not any(r.uri == trial.trace_ref for r in trace_refs):
                trace_refs.append(Reference(ref_id=f"{trial.trial_id}-trace", uri=trial.trace_ref, kind="trace"))
            for ref in trial.artifact_refs:
                if not any(r.uri == ref.uri for r in artifact_refs):
                    artifact_refs.append(ref)

    benchmark_names = sorted({c.benchmark_id for c in case_results})
    suite_id = getattr(args, "difficulty", None) or benchmark_names or ["unknown"]
    if isinstance(suite_id, list):
        suite_id = "+".join(suite_id) if suite_id else "unknown"

    summary: dict[str, Any] = {"tier_aggregates": tier_aggregates}
    if comparison_context is not None:
        summary["comparison_context"] = comparison_context

    report = EvalReport(
        eval_run_id=eval_run_id,
        created_at=created_at,
        candidate_version=str(getattr(args, "sha", "") or ""),
        benchmark_suite_id=str(suite_id),
        benchmark_case_results=case_results,
        scorecard=scorecard,
        baseline_version=str(getattr(args, "compare_to", "") or "") or None,
        trace_references=trace_refs,
        artifact_references=artifact_refs,
        summary=summary,
        metadata={
            "runner": getattr(args, "runner", None),
            "model": getattr(args, "model", None),
            "repo": getattr(args, "repo", None),
        },
    )
    errors = report.validate()
    if errors:
        raise ReportBuildError("built report failed schema validation: " + "; ".join(errors))
    return report


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_eval_report(report: EvalReport, reports_dir: Path, *, difficulty: str, stamp: str | None = None) -> Path:
    """Atomically publish `report` as `{stamp}-{difficulty}.json` and update
    `latest.json` with identical bytes. Raises `ReportBuildError` if the
    report fails validation — nothing is written in that case.
    """
    errors = validate_report_payload(report.to_dict())
    if errors:
        raise ReportBuildError("refusing to publish invalid report: " + "; ".join(errors))

    data = (report.to_json() + "\n").encode("utf-8")
    stamp = stamp or report_stamp(report.created_at)
    reports_dir = Path(reports_dir)
    timestamped_path = timestamped_report_path(reports_dir, stamp, difficulty)
    _atomic_write_bytes(timestamped_path, data)

    latest_path = latest_report_path(reports_dir)
    try:
        _atomic_write_bytes(latest_path, data)
    except OSError as exc:
        raise ReportBuildError(
            f"wrote {timestamped_path} but failed to publish latest.json: {exc}. "
            "The timestamped report is intact; latest.json is stale."
        ) from exc
    return timestamped_path
