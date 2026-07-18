"""Human-readable rendering of a v0.2 `EvalReport`.

`render_eval_summary` reads ONLY canonical `EvalReport`/`Metric`/`Scorecard`
fields already present on the report. It performs plain counting/aggregation
for display (e.g. "N cases, M trials") but never applies comparison policy
and never derives a pass/fail verdict beyond what the report itself records.
Missing metrics are shown as `unavailable (<reason>)` — never coerced to 0,
matching the same honesty contract `eval/live_report.py` builds reports with.
"""
from __future__ import annotations

from eval.report_schema import AcStatus, CaseStatus, EvalReport, Metric, MetricStatus


def _fmt_metric(metric: Metric, *, pct: bool = False) -> str:
    if metric.status != MetricStatus.KNOWN:
        reason = metric.unavailable_reason or "no reason given"
        return f"unavailable ({metric.status.value}: {reason})"
    value = metric.value
    if pct and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.1%}"
    return str(value)


def render_eval_summary(report: EvalReport, *, report_path: str | None = None) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"EVAL REPORT SUMMARY  (schema v{report.report_schema_version})")
    lines.append("=" * 72)
    lines.append(f"eval_run_id:        {report.eval_run_id}")
    lines.append(f"created_at:         {report.created_at}")
    lines.append(f"candidate_version:  {report.candidate_version}")
    lines.append(f"baseline_version:   {report.baseline_version if report.baseline_version is not None else 'unavailable'}")
    lines.append(f"benchmark_suite_id: {report.benchmark_suite_id}")
    if report_path is not None:
        lines.append(f"report_path:        {report_path}")
    lines.append("")

    cases = report.benchmark_case_results
    trials = [t for case in cases for t in case.trial_results]
    case_pass = sum(1 for c in cases if c.status == CaseStatus.PASSED)
    known_acs = [
        ac for case in cases for ac in case.acceptance_criteria_results if ac.status in (AcStatus.PASS, AcStatus.FAIL)
    ]
    ac_pass = sum(1 for ac in known_acs if ac.status == AcStatus.PASS)

    lines.append("Cases and trials:")
    lines.append(f"  benchmark cases: {len(cases)} ({case_pass} passed)")
    lines.append(f"  trials:          {len(trials)}")
    lines.append(f"  acceptance criteria (known status): {len(known_acs)} ({ac_pass} passed)")
    lines.append("")

    scorecard = report.scorecard
    lines.append("Scorecard:")
    lines.append("  capability:")
    lines.append(f"    ac_pass_rate:            {_fmt_metric(scorecard.capability.ac_pass_rate, pct=True)}")
    lines.append(f"    critical_ac_pass_rate:   {_fmt_metric(scorecard.capability.critical_ac_pass_rate, pct=True)}")
    lines.append(f"    hidden_test_pass_rate:   {_fmt_metric(scorecard.capability.hidden_test_pass_rate, pct=True)}")
    lines.append(f"    full_benchmark_pass_rate:{_fmt_metric(scorecard.capability.full_benchmark_pass_rate, pct=True)}")
    lines.append("  reliability:")
    lines.append(f"    trial_pass_rate:         {_fmt_metric(scorecard.reliability.trial_pass_rate, pct=True)}")
    lines.append(f"    variance:                {_fmt_metric(scorecard.reliability.variance)}")
    lines.append(f"    flaky_case_count:        {_fmt_metric(scorecard.reliability.flaky_case_count)}")
    lines.append(f"    workflow_failure_rate:   {_fmt_metric(scorecard.reliability.workflow_failure_rate, pct=True)}")
    lines.append("  efficiency:")
    lines.append(f"    wall_clock_duration_ms:  {_fmt_metric(scorecard.efficiency.wall_clock_duration_ms)}")
    lines.append(f"    agent_invocation_count:  {_fmt_metric(scorecard.efficiency.agent_invocation_count)}")
    lines.append(f"    loop_iteration_count:    {_fmt_metric(scorecard.efficiency.loop_iteration_count)}")
    lines.append(f"    token_usage:             {_fmt_metric(scorecard.efficiency.token_usage)}")
    lines.append(f"    estimated_cost:          {_fmt_metric(scorecard.efficiency.estimated_cost)}")
    lines.append("  traceability:")
    lines.append(f"    trace_present:           {_fmt_metric(scorecard.traceability.trace_present)}")
    lines.append(f"    test_output_present:     {_fmt_metric(scorecard.traceability.test_output_present)}")
    lines.append(f"    final_artifact_refs_present: {_fmt_metric(scorecard.traceability.final_artifact_refs_present)}")
    lines.append(f"    final_diff_ref_present:  {_fmt_metric(scorecard.traceability.final_diff_ref_present)}")
    lines.append(f"    prompt_hashes_present:   {_fmt_metric(scorecard.traceability.prompt_hashes_present)}")
    lines.append("")

    if report.trace_references:
        lines.append("Trace references:")
        for ref in report.trace_references:
            lines.append(f"  - [{ref.kind}] {ref.ref_id}: {ref.uri}")
        lines.append("")
    else:
        lines.append("Trace references: (none)")
        lines.append("")

    if report.artifact_references:
        lines.append("Artifact references:")
        for ref in report.artifact_references:
            lines.append(f"  - [{ref.kind}] {ref.ref_id}: {ref.uri}")
        lines.append("")

    failed_cases = [c for c in cases if c.status != CaseStatus.PASSED]
    if failed_cases:
        lines.append("Non-passing cases:")
        for case in failed_cases:
            lines.append(f"  - {case.benchmark_id} (difficulty={case.difficulty}): {case.status.value}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
