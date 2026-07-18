"""Centralized classification policy for a v0.2 report comparison.

`classify()` is the single place that turns computed deltas into one of the
five `Classification` values. `eval.comparison` builds the deltas; nothing
outside this module decides `improved` vs `regressed` vs `mixed` vs
`unchanged` vs `inconclusive` — in particular `eval.render_comparison` must
never re-derive or second-guess a classification, only display it.

Precedence (documented, evaluated in this exact order):
1. Incomparable input (schema/version mismatch) is not handled here at all —
   `eval.comparison` raises `CompareInputError` before a result exists.
2. Insufficient evidence / non-comparable state -> `inconclusive`.
3. Hard gates (any one triggers `regressed` regardless of anything else).
4. Material improvements and material regressions both present -> `mixed`.
5. Only material regressions present (below hard-gate severity) -> `mixed`
   (documented deviation: an unresolved material regression is never
   silently folded into `unchanged`, even with no offsetting improvement).
6. Only material improvements present -> `improved`.
7. Nothing material either way -> `unchanged`.
"""
from __future__ import annotations

from eval.comparison_policy import ComparisonPolicy, ReasonCode
from eval.comparison_schema import (
    AcDelta,
    BenchmarkDelta,
    ChangeCategory,
    Classification,
    ClassificationReason,
    Comparability,
    DeltaAvailability,
    DriftFinding,
    DriftSeverity,
    EvidenceFieldDelta,
    MetricDelta,
    ReasonSeverity,
)


def _pct(fraction: float) -> str:
    return f"{fraction:+.1%}"


def classify(
    *,
    comparability: Comparability,
    drift: list[DriftFinding],
    ac_deltas: list[AcDelta],
    benchmark_deltas: list[BenchmarkDelta],
    scorecard_deltas: dict[str, dict[str, MetricDelta]],
    evidence_deltas: list[EvidenceFieldDelta],
    policy: ComparisonPolicy,
) -> tuple[Classification, list[ClassificationReason]]:
    # --- step 2: insufficient evidence / non-comparable ---
    if comparability == Comparability.INCOMPARABLE:
        return Classification.INCONCLUSIVE, [
            ClassificationReason(
                code=ReasonCode.NO_SHARED_IDENTITY,
                severity=ReasonSeverity.CRITICAL,
                summary="baseline and candidate reports are not meaningfully comparable",
            )
        ]

    required_unavailable = [e for e in evidence_deltas if e.requirement == "required" and e.degraded]
    if required_unavailable:
        return Classification.INCONCLUSIVE, [
            ClassificationReason(
                code=ReasonCode.REQUIRED_EVIDENCE_UNAVAILABLE,
                severity=ReasonSeverity.CRITICAL,
                summary=f"required evidence '{e.field}' is unavailable on the candidate; cannot draw a conclusion",
                metric_refs=[f"traceability.{e.field}"],
            )
            for e in required_unavailable
        ]

    # --- step 3: hard gates ---
    hard: list[ClassificationReason] = []

    easy_regressions = [d for d in benchmark_deltas if d.easy_regression]
    if easy_regressions:
        hard.append(
            ClassificationReason(
                code=ReasonCode.EASY_BENCHMARK_REGRESSION,
                severity=ReasonSeverity.CRITICAL,
                summary=f"{len(easy_regressions)} easy-tier benchmark case(s) regressed",
                benchmark_ids=[d.benchmark_id for d in easy_regressions],
            )
        )

    critical_ac_regressions = [d for d in ac_deltas if d.critical and d.category == ChangeCategory.REGRESSED]
    if critical_ac_regressions:
        hard.append(
            ClassificationReason(
                code=ReasonCode.CRITICAL_AC_REGRESSION,
                severity=ReasonSeverity.CRITICAL,
                summary=f"{len(critical_ac_regressions)} critical acceptance criterion/criteria regressed",
                ac_ids=[f"{d.benchmark_id}/{d.ac_id}" for d in critical_ac_regressions],
            )
        )

    ac_rate = scorecard_deltas.get("capability", {}).get("ac_pass_rate")
    if (
        ac_rate
        and ac_rate.availability == DeltaAvailability.COMPUTED
        and ac_rate.absolute_delta is not None
        and ac_rate.absolute_delta <= -policy.ac_pass_rate_materiality
    ):
        hard.append(
            ClassificationReason(
                code=ReasonCode.AC_PASS_RATE_DROP,
                severity=ReasonSeverity.CRITICAL,
                summary=f"overall AC pass rate changed by {_pct(ac_rate.absolute_delta)}",
                metric_refs=["capability.ac_pass_rate"],
            )
        )

    wf_rate = scorecard_deltas.get("reliability", {}).get("workflow_failure_rate")
    if (
        wf_rate
        and wf_rate.availability == DeltaAvailability.COMPUTED
        and wf_rate.absolute_delta is not None
        and wf_rate.absolute_delta >= policy.workflow_failure_rate_materiality
    ):
        hard.append(
            ClassificationReason(
                code=ReasonCode.WORKFLOW_FAILURE_RATE_INCREASE,
                severity=ReasonSeverity.CRITICAL,
                summary=f"workflow failure rate changed by {_pct(wf_rate.absolute_delta)}",
                metric_refs=["reliability.workflow_failure_rate"],
            )
        )

    required_lost = [e for e in evidence_deltas if e.requirement == "required" and e.regressed]
    if required_lost:
        hard.append(
            ClassificationReason(
                code=ReasonCode.REQUIRED_EVIDENCE_LOST,
                severity=ReasonSeverity.CRITICAL,
                summary=f"required evidence field(s) regressed from present to absent: {[e.field for e in required_lost]}",
                metric_refs=[f"traceability.{e.field}" for e in required_lost],
            )
        )

    runtime = scorecard_deltas.get("efficiency", {}).get("wall_clock_duration_ms")
    if (
        runtime
        and runtime.availability == DeltaAvailability.COMPUTED
        and runtime.relative_delta is not None
        and runtime.relative_delta * 100 >= policy.runtime_increase_hard_budget_pct
    ):
        hard.append(
            ClassificationReason(
                code=ReasonCode.RUNTIME_HARD_BUDGET_BREACH,
                severity=ReasonSeverity.CRITICAL,
                summary=f"wall-clock duration increased {_pct(runtime.relative_delta)}, exceeding the hard budget ({policy.runtime_increase_hard_budget_pct}%)",
                metric_refs=["efficiency.wall_clock_duration_ms"],
            )
        )

    cost = scorecard_deltas.get("efficiency", {}).get("estimated_cost")
    if (
        cost
        and cost.availability == DeltaAvailability.COMPUTED
        and cost.relative_delta is not None
        and cost.relative_delta * 100 >= policy.cost_increase_hard_budget_pct
    ):
        hard.append(
            ClassificationReason(
                code=ReasonCode.COST_HARD_BUDGET_BREACH,
                severity=ReasonSeverity.CRITICAL,
                summary=f"estimated cost increased {_pct(cost.relative_delta)}, exceeding the hard budget ({policy.cost_increase_hard_budget_pct}%)",
                metric_refs=["efficiency.estimated_cost"],
            )
        )

    if any(d.severity == DriftSeverity.INCOMPATIBLE for d in drift):
        hard.append(
            ClassificationReason(
                code=ReasonCode.SUITE_MISMATCH,
                severity=ReasonSeverity.CRITICAL,
                summary="incompatible configuration drift detected between baseline and candidate",
            )
        )

    if hard:
        return Classification.REGRESSED, hard

    # --- steps 4-7: soft improvements vs. soft regressions ---
    improvements: list[ClassificationReason] = []
    regressions: list[ClassificationReason] = []

    critical_ac_improvements = [d for d in ac_deltas if d.critical and d.category == ChangeCategory.IMPROVED]
    if critical_ac_improvements:
        improvements.append(
            ClassificationReason(
                code=ReasonCode.CRITICAL_AC_IMPROVEMENT,
                severity=ReasonSeverity.INFO,
                summary=f"{len(critical_ac_improvements)} critical acceptance criterion/criteria improved",
                ac_ids=[f"{d.benchmark_id}/{d.ac_id}" for d in critical_ac_improvements],
            )
        )

    if (
        ac_rate
        and ac_rate.availability == DeltaAvailability.COMPUTED
        and ac_rate.absolute_delta is not None
        and ac_rate.absolute_delta >= policy.ac_pass_rate_materiality
    ):
        improvements.append(
            ClassificationReason(
                code=ReasonCode.AC_PASS_RATE_GAIN,
                severity=ReasonSeverity.INFO,
                summary=f"overall AC pass rate changed by {_pct(ac_rate.absolute_delta)}",
                metric_refs=["capability.ac_pass_rate"],
            )
        )

    if (
        wf_rate
        and wf_rate.availability == DeltaAvailability.COMPUTED
        and wf_rate.absolute_delta is not None
        and wf_rate.absolute_delta <= -policy.workflow_failure_rate_materiality
    ):
        improvements.append(
            ClassificationReason(
                code=ReasonCode.WORKFLOW_FAILURE_RATE_DECREASE,
                severity=ReasonSeverity.INFO,
                summary=f"workflow failure rate changed by {_pct(wf_rate.absolute_delta)}",
                metric_refs=["reliability.workflow_failure_rate"],
            )
        )

    resolved = [d for d in benchmark_deltas if d.resolved_failure]
    if resolved:
        improvements.append(
            ClassificationReason(
                code=ReasonCode.CLEAN_IMPROVEMENT,
                severity=ReasonSeverity.INFO,
                summary=f"{len(resolved)} previously-failing benchmark case(s) now pass",
                benchmark_ids=[d.benchmark_id for d in resolved],
            )
        )

    trial_rate = scorecard_deltas.get("reliability", {}).get("trial_pass_rate")
    if (
        trial_rate
        and trial_rate.availability == DeltaAvailability.COMPUTED
        and trial_rate.absolute_delta is not None
    ):
        if trial_rate.absolute_delta >= policy.trial_pass_rate_materiality:
            improvements.append(
                ClassificationReason(
                    code=ReasonCode.RELIABILITY_IMPROVEMENT,
                    severity=ReasonSeverity.INFO,
                    summary=f"trial pass rate changed by {_pct(trial_rate.absolute_delta)}",
                    metric_refs=["reliability.trial_pass_rate"],
                )
            )
        elif trial_rate.absolute_delta <= -policy.trial_pass_rate_materiality:
            regressions.append(
                ClassificationReason(
                    code=ReasonCode.RELIABILITY_REGRESSION,
                    severity=ReasonSeverity.WARNING,
                    summary=f"trial pass rate changed by {_pct(trial_rate.absolute_delta)}",
                    metric_refs=["reliability.trial_pass_rate"],
                )
            )

    noncritical_regressions = [d for d in ac_deltas if not d.critical and d.category == ChangeCategory.REGRESSED]
    if noncritical_regressions:
        regressions.append(
            ClassificationReason(
                code=ReasonCode.UNRESOLVED_MATERIAL_REGRESSION,
                severity=ReasonSeverity.WARNING,
                summary=f"{len(noncritical_regressions)} non-critical acceptance criterion/criteria regressed",
                ac_ids=[f"{d.benchmark_id}/{d.ac_id}" for d in noncritical_regressions],
            )
        )

    new_failures = [d for d in benchmark_deltas if d.newly_failing and not d.easy_regression]
    if new_failures:
        regressions.append(
            ClassificationReason(
                code=ReasonCode.UNRESOLVED_MATERIAL_REGRESSION,
                severity=ReasonSeverity.WARNING,
                summary=f"{len(new_failures)} benchmark case(s) newly failing",
                benchmark_ids=[d.benchmark_id for d in new_failures],
            )
        )

    conditional_lost = [e for e in evidence_deltas if e.requirement == "conditional" and e.regressed]
    if conditional_lost:
        regressions.append(
            ClassificationReason(
                code=ReasonCode.CONDITIONAL_EVIDENCE_LOST,
                severity=ReasonSeverity.WARNING,
                summary=f"conditional evidence field(s) regressed from present to absent: {[e.field for e in conditional_lost]}",
                metric_refs=[f"traceability.{e.field}" for e in conditional_lost],
            )
        )

    if (
        runtime
        and runtime.availability == DeltaAvailability.COMPUTED
        and runtime.relative_delta is not None
        and runtime.relative_delta * 100 >= policy.runtime_increase_soft_budget_pct
    ):
        regressions.append(
            ClassificationReason(
                code=ReasonCode.RUNTIME_SOFT_BUDGET_BREACH,
                severity=ReasonSeverity.WARNING,
                summary=f"wall-clock duration increased {_pct(runtime.relative_delta)}, exceeding the soft budget ({policy.runtime_increase_soft_budget_pct}%)",
                metric_refs=["efficiency.wall_clock_duration_ms"],
            )
        )

    if (
        cost
        and cost.availability == DeltaAvailability.COMPUTED
        and cost.relative_delta is not None
        and cost.relative_delta * 100 >= policy.cost_increase_soft_budget_pct
    ):
        regressions.append(
            ClassificationReason(
                code=ReasonCode.COST_SOFT_BUDGET_BREACH,
                severity=ReasonSeverity.WARNING,
                summary=f"estimated cost increased {_pct(cost.relative_delta)}, exceeding the soft budget ({policy.cost_increase_soft_budget_pct}%)",
                metric_refs=["efficiency.estimated_cost"],
            )
        )

    if any(d.severity == DriftSeverity.MATERIAL for d in drift):
        regressions.append(
            ClassificationReason(
                code=ReasonCode.MATERIAL_DRIFT,
                severity=ReasonSeverity.WARNING,
                summary="material configuration drift detected between baseline and candidate",
            )
        )

    if improvements and regressions:
        return Classification.MIXED, [
            ClassificationReason(
                code=ReasonCode.MIXED_MOVEMENT,
                severity=ReasonSeverity.WARNING,
                summary="both material improvements and material regressions were detected",
            ),
            *improvements,
            *regressions,
        ]
    if regressions:
        # Documented deviation: an unresolved material regression with no
        # offsetting improvement is reported as `mixed`, not silently folded
        # into `unchanged` -- it still needs a human to look at it.
        return Classification.MIXED, regressions
    if improvements:
        return Classification.IMPROVED, improvements
    return Classification.UNCHANGED, [
        ClassificationReason(
            code=ReasonCode.NO_MATERIAL_CHANGE,
            severity=ReasonSeverity.INFO,
            summary="no material capability, reliability, or efficiency movement detected",
        )
    ]
