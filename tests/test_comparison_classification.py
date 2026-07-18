"""Classification-precedence tests for `eval.classification.classify` (Prompt 5).

Covers coverage categories 41-53: exercises `classify()` directly against
constructed deltas/policy rather than always going through
`compare_reports()`, so precedence can be tested in isolation (e.g. "a hard
gate wins even when material improvements are also present").
"""
from __future__ import annotations

import unittest

from eval.comparison_policy import ComparisonPolicy, ReasonCode
from eval.classification import classify
from eval.comparison_schema import (
    AcDelta,
    BenchmarkDelta,
    ChangeCategory,
    Classification,
    Comparability,
    DeltaAvailability,
    DriftFinding,
    DriftSeverity,
    EvidenceFieldDelta,
    MetricDelta,
)

POLICY = ComparisonPolicy()


def _metric(name: str, absolute_delta, relative_delta=None, availability=DeltaAvailability.COMPUTED):
    return MetricDelta(
        name=name,
        baseline_status="known",
        baseline_value=0,
        candidate_status="known",
        candidate_value=0,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        availability=availability,
    )


def _empty_scorecard():
    return {"capability": {}, "reliability": {}, "efficiency": {}}


def _ac(benchmark_id="b1", ac_id="AC1", critical=False, category=ChangeCategory.UNCHANGED):
    return AcDelta(
        benchmark_id=benchmark_id,
        ac_id=ac_id,
        critical=critical,
        baseline_status="pass",
        candidate_status="pass",
        baseline_difficulty="medium",
        candidate_difficulty="medium",
        category=category,
    )


def _benchmark(benchmark_id="b1", category=ChangeCategory.UNCHANGED, easy_regression=False, newly_failing=False, resolved_failure=False):
    return BenchmarkDelta(
        benchmark_id=benchmark_id,
        baseline_difficulty="medium",
        candidate_difficulty="medium",
        baseline_status="passed",
        candidate_status="passed",
        baseline_ac_pass_rate=1.0,
        candidate_ac_pass_rate=1.0,
        baseline_trial_count=1,
        candidate_trial_count=1,
        trial_count_comparable=True,
        category=category,
        newly_failing=newly_failing,
        resolved_failure=resolved_failure,
        easy_regression=easy_regression,
    )


def _evidence(field="trace_present", requirement="required", regressed=False, degraded=False, gained=False):
    return EvidenceFieldDelta(
        field=field,
        requirement=requirement,
        baseline_status="known",
        baseline_value=True,
        candidate_status="known" if not degraded else "not_collected",
        candidate_value=False if regressed else True,
        regressed=regressed,
        degraded=degraded,
        gained=gained,
    )


class IncomparableAndEvidenceTests(unittest.TestCase):
    def test_incomparable_input_yields_inconclusive(self):
        classification, reasons = classify(
            comparability=Comparability.INCOMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.INCONCLUSIVE)
        self.assertEqual(reasons[0].code, ReasonCode.NO_SHARED_IDENTITY)

    def test_required_evidence_degraded_yields_inconclusive_even_with_improvements(self):
        classification, reasons = classify(
            comparability=Comparability.DEGRADED,
            drift=[],
            ac_deltas=[_ac(critical=True, category=ChangeCategory.IMPROVED)],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[_evidence(degraded=True)],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.INCONCLUSIVE)
        self.assertEqual(reasons[0].code, ReasonCode.REQUIRED_EVIDENCE_UNAVAILABLE)

    def test_inconclusive_takes_precedence_over_incomparable_drift_hard_gate(self):
        # comparability incomparable should short-circuit before even
        # looking at drift-based hard gates.
        classification, reasons = classify(
            comparability=Comparability.INCOMPARABLE,
            drift=[DriftFinding(field="benchmark_suite_id", baseline_value="a", candidate_value="b", severity=DriftSeverity.INCOMPATIBLE, explanation="x")],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.INCONCLUSIVE)
        self.assertEqual(len(reasons), 1)
        self.assertEqual(reasons[0].code, ReasonCode.NO_SHARED_IDENTITY)


class HardGatePrecedenceTests(unittest.TestCase):
    def test_easy_regression_forces_regressed_even_with_improvements_present(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=True, category=ChangeCategory.IMPROVED)],
            benchmark_deltas=[_benchmark(benchmark_id="easy-1", category=ChangeCategory.REGRESSED, easy_regression=True)],
            scorecard_deltas={
                "capability": {"ac_pass_rate": _metric("ac_pass_rate", 0.5, 0.5)},
                "reliability": {},
                "efficiency": {},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        codes = {r.code for r in reasons}
        self.assertIn(ReasonCode.EASY_BENCHMARK_REGRESSION, codes)

    def test_critical_ac_regression_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=True, category=ChangeCategory.REGRESSED)],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.CRITICAL_AC_REGRESSION)

    def test_ac_pass_rate_material_drop_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {"ac_pass_rate": _metric("ac_pass_rate", -0.10, -0.10)},
                "reliability": {},
                "efficiency": {},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.AC_PASS_RATE_DROP)

    def test_ac_pass_rate_drop_below_materiality_is_not_a_hard_gate(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {"ac_pass_rate": _metric("ac_pass_rate", -0.01, -0.01)},
                "reliability": {},
                "efficiency": {},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertNotEqual(classification, Classification.REGRESSED)

    def test_workflow_failure_rate_increase_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {},
                "reliability": {"workflow_failure_rate": _metric("workflow_failure_rate", 0.10, 0.10)},
                "efficiency": {},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.WORKFLOW_FAILURE_RATE_INCREASE)

    def test_required_evidence_lost_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[_evidence(regressed=True)],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.REQUIRED_EVIDENCE_LOST)

    def test_runtime_hard_budget_breach_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {},
                "reliability": {},
                "efficiency": {"wall_clock_duration_ms": _metric("wall_clock_duration_ms", 200000, 0.60)},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.RUNTIME_HARD_BUDGET_BREACH)

    def test_runtime_soft_breach_alone_is_not_a_hard_gate(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {},
                "reliability": {},
                "efficiency": {"wall_clock_duration_ms": _metric("wall_clock_duration_ms", 60000, 0.20)},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertNotEqual(classification, Classification.REGRESSED)
        self.assertIn(ReasonCode.RUNTIME_SOFT_BUDGET_BREACH, {r.code for r in reasons})

    def test_cost_hard_budget_breach_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas={
                "capability": {},
                "reliability": {},
                "efficiency": {"estimated_cost": _metric("estimated_cost", 1.0, 0.55)},
            },
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.COST_HARD_BUDGET_BREACH)

    def test_incompatible_drift_forces_regressed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[DriftFinding(field="benchmark_suite_id", baseline_value="a", candidate_value="b", severity=DriftSeverity.INCOMPATIBLE, explanation="x")],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        self.assertEqual(reasons[0].code, ReasonCode.SUITE_MISMATCH)

    def test_multiple_hard_gates_all_reported(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=True, category=ChangeCategory.REGRESSED)],
            benchmark_deltas=[_benchmark(benchmark_id="easy-1", category=ChangeCategory.REGRESSED, easy_regression=True)],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.REGRESSED)
        codes = {r.code for r in reasons}
        self.assertIn(ReasonCode.CRITICAL_AC_REGRESSION, codes)
        self.assertIn(ReasonCode.EASY_BENCHMARK_REGRESSION, codes)


class SoftMovementTests(unittest.TestCase):
    def test_only_improvements_yields_improved(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=True, category=ChangeCategory.IMPROVED)],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.IMPROVED)
        self.assertEqual(reasons[0].code, ReasonCode.CRITICAL_AC_IMPROVEMENT)

    def test_no_material_change_yields_unchanged(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=False, category=ChangeCategory.UNCHANGED)],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.UNCHANGED)
        self.assertEqual(reasons[0].code, ReasonCode.NO_MATERIAL_CHANGE)

    def test_only_soft_regressions_is_mixed_not_unchanged(self):
        """Documented deviation: an unresolved material regression alone is
        `mixed`, never silently folded into `unchanged`."""
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[_ac(critical=False, category=ChangeCategory.REGRESSED)],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.MIXED)
        self.assertEqual(reasons[0].code, ReasonCode.UNRESOLVED_MATERIAL_REGRESSION)

    def test_improvements_and_regressions_together_is_mixed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[
                _ac(critical=True, ac_id="AC1", category=ChangeCategory.IMPROVED),
                _ac(critical=False, ac_id="AC2", category=ChangeCategory.REGRESSED),
            ],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.MIXED)
        self.assertEqual(reasons[0].code, ReasonCode.MIXED_MOVEMENT)

    def test_material_drift_alone_is_mixed(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[DriftFinding(field="metadata.model", baseline_value="a", candidate_value="b", severity=DriftSeverity.MATERIAL, explanation="x")],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.MIXED)
        self.assertEqual(reasons[0].code, ReasonCode.MATERIAL_DRIFT)

    def test_conditional_evidence_lost_is_soft_regression(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[_evidence(field="final_diff_ref_present", requirement="conditional", regressed=True)],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.MIXED)
        self.assertEqual(reasons[0].code, ReasonCode.CONDITIONAL_EVIDENCE_LOST)

    def test_resolved_failure_reported_as_clean_improvement(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[_benchmark(benchmark_id="medium-1", category=ChangeCategory.IMPROVED, resolved_failure=True)],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.IMPROVED)
        self.assertEqual(reasons[0].code, ReasonCode.CLEAN_IMPROVEMENT)

    def test_new_non_easy_failure_reported_as_unresolved_regression(self):
        classification, reasons = classify(
            comparability=Comparability.COMPARABLE,
            drift=[],
            ac_deltas=[],
            benchmark_deltas=[_benchmark(benchmark_id="medium-1", category=ChangeCategory.REGRESSED, newly_failing=True, easy_regression=False)],
            scorecard_deltas=_empty_scorecard(),
            evidence_deltas=[],
            policy=POLICY,
        )
        self.assertEqual(classification, Classification.MIXED)
        self.assertEqual(reasons[0].code, ReasonCode.UNRESOLVED_MATERIAL_REGRESSION)


if __name__ == "__main__":
    unittest.main()
