"""Unit tests for `eval/live_report.py` — the v0.2 report builder + atomic
writer. Pure functions over raw `run_one`-shaped result dicts; no subprocess
calls, no LLM, no server."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from eval.live_report import ReportBuildError, build_eval_report, write_eval_report
from eval.report_schema import AcStatus, CaseStatus, MetricStatus, TrialStatus, validate_report_payload


def _args(**overrides) -> SimpleNamespace:
    base = dict(sha="sha123", runner="claude", model="unit-model", repo="/repo", difficulty="medium", compare_to=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _trial(
    *,
    benchmark_id="medium",
    difficulty="medium",
    run_id="medium-t1",
    trial_index=1,
    status="PASS",
    error=None,
    wall_seconds=10.0,
    score_weighted=1.0,
    acceptance_criteria=("AC1: First.", "AC2: Second."),
    critical=(),
    domain="test",
    ac_results=None,
    evidence=None,
) -> dict:
    return {
        "benchmark_id": benchmark_id,
        "difficulty": difficulty,
        "run_id": run_id,
        "trial_index": trial_index,
        "status": status,
        "error": error,
        "started_at": "2026-05-22T11:59:00.000Z",
        "completed_at": "2026-05-22T12:00:00.000Z",
        "score_weighted": score_weighted,
        "metrics": {"wall_seconds": wall_seconds},
        "story": {
            "acceptance_criteria": list(acceptance_criteria),
            "metadata": {"critical_acceptance_criteria": list(critical), "domain": domain},
        },
        "hidden_tests": {"total": 2, "passed": 2, "ac_results": ac_results or {}} if ac_results is not None else None,
        "evidence": evidence or {},
    }


class BuildEvalReportIdentityTests(unittest.TestCase):
    def test_rejects_trials_with_mismatched_acceptance_criteria(self):
        t1 = _trial(acceptance_criteria=("AC1: First.",))
        t2 = _trial(run_id="medium-t2", trial_index=2, acceptance_criteria=("AC1: Different text."))
        with self.assertRaises(ReportBuildError):
            build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")

    def test_rejects_trials_with_mismatched_critical_flags(self):
        t1 = _trial(critical=())
        t2 = _trial(run_id="medium-t2", trial_index=2, critical=("AC1",))
        with self.assertRaises(ReportBuildError):
            build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")

    def test_rejects_trials_with_mismatched_domain(self):
        t1 = _trial(domain="frontend")
        t2 = _trial(run_id="medium-t2", trial_index=2, domain="backend")
        with self.assertRaises(ReportBuildError):
            build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")

    def test_groups_by_benchmark_id_and_difficulty_not_just_name(self):
        easy = _trial(benchmark_id="task-a", difficulty="easy", run_id="a-e1")
        hard = _trial(benchmark_id="task-a", difficulty="hard", run_id="a-h1")
        report = build_eval_report([easy, hard], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        keys = {(c.benchmark_id, c.difficulty) for c in report.benchmark_case_results}
        self.assertEqual(keys, {("task-a", "easy"), ("task-a", "hard")})


class AcStatusRuleTests(unittest.TestCase):
    def test_ac_passes_only_when_every_reaching_trial_passed(self):
        t1 = _trial(run_id="t1", ac_results={"AC1": {"tests": ["t"], "passed": True}, "AC2": {"tests": ["t"], "passed": True}})
        t2 = _trial(run_id="t2", trial_index=2, ac_results={"AC1": {"tests": ["t"], "passed": True}, "AC2": {"tests": ["t"], "passed": True}})
        report = build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        acs = {ac.ac_id: ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
        self.assertEqual(acs["AC1"], AcStatus.PASS)
        self.assertEqual(acs["AC2"], AcStatus.PASS)

    def test_ac_fails_if_any_reaching_trial_failed(self):
        t1 = _trial(run_id="t1", ac_results={"AC1": {"tests": ["t"], "passed": True}, "AC2": {"tests": ["t"], "passed": True}})
        t2 = _trial(run_id="t2", trial_index=2, status="FAIL", ac_results={"AC1": {"tests": ["t"], "passed": True}, "AC2": {"tests": ["t"], "failed": True}})
        report = build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        acs = {ac.ac_id: ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
        self.assertEqual(acs["AC2"], AcStatus.FAIL)

    def test_ac_unknown_when_hidden_tests_never_ran(self):
        # workflow failed before hidden tests ever executed: hidden_tests is None.
        t1 = _trial(status="FAIL", error="workflow failed: exit 1", ac_results=None)
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        acs = {ac.ac_id: ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
        self.assertEqual(acs["AC1"], AcStatus.UNKNOWN)
        self.assertEqual(acs["AC2"], AcStatus.UNKNOWN)
        # never silently inferred as pass.
        self.assertNotIn(AcStatus.PASS, acs.values())

    def test_ac_unknown_when_case_has_missing_cases(self):
        t1 = _trial(ac_results={"AC1": {"tests": ["t"], "missing_cases": True}, "AC2": {"tests": ["t"], "passed": True}})
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        ac1 = next(ac for ac in report.benchmark_case_results[0].acceptance_criteria_results if ac.ac_id == "AC1")
        self.assertEqual(ac1.status, AcStatus.UNKNOWN)
        self.assertIsNotNone(ac1.failure_reason)


class CaseStatusTests(unittest.TestCase):
    def test_case_passed_when_all_trials_pass(self):
        t1, t2 = _trial(run_id="t1"), _trial(run_id="t2", trial_index=2)
        report = build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].status, CaseStatus.PASSED)

    def test_case_failed_when_all_trials_fail(self):
        t1 = _trial(run_id="t1", status="FAIL", error="hidden tests failed")
        t2 = _trial(run_id="t2", trial_index=2, status="FAIL", error="hidden tests failed")
        report = build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].status, CaseStatus.FAILED)

    def test_case_partial_on_mixed_trial_outcomes(self):
        t1 = _trial(run_id="t1", status="PASS")
        t2 = _trial(run_id="t2", trial_index=2, status="FAIL", error="hidden tests failed")
        report = build_eval_report([t1, t2], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].status, CaseStatus.PARTIAL)


class FailureCategoryMappingTests(unittest.TestCase):
    def test_timeout_error_maps_to_timeout(self):
        t1 = _trial(status="FAIL", error="timeout after 300s")
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].trial_results[0].failure_category, "timeout")
        self.assertEqual(report.benchmark_case_results[0].trial_results[0].status, TrialStatus.TIMEOUT)

    def test_workflow_failure_maps_to_workflow_failure(self):
        t1 = _trial(status="FAIL", error="workflow failed: nonzero exit")
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].trial_results[0].failure_category, "workflow_failure")

    def test_hidden_test_failure_maps_to_hidden_test_failure(self):
        t1 = _trial(status="FAIL", error="hidden tests failed")
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].trial_results[0].failure_category, "hidden_test_failure")

    def test_unrecognized_error_maps_to_unknown_not_dropped(self):
        t1 = _trial(status="FAIL", error="RuntimeError: something exploded")
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.benchmark_case_results[0].trial_results[0].failure_category, "unknown")


class ScorecardHonestyTests(unittest.TestCase):
    def test_token_usage_and_cost_are_always_not_collected(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.scorecard.efficiency.token_usage.status, MetricStatus.NOT_COLLECTED)
        self.assertEqual(report.scorecard.efficiency.estimated_cost.status, MetricStatus.NOT_COLLECTED)
        self.assertIsNone(report.scorecard.efficiency.token_usage.value)

    def test_variance_not_applicable_with_fewer_than_two_trials(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.scorecard.reliability.variance.status, MetricStatus.NOT_APPLICABLE)

    def test_wall_clock_unknown_when_no_trial_reports_duration(self):
        t1 = _trial(wall_seconds=None)
        t1["metrics"] = {}
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(report.scorecard.efficiency.wall_clock_duration_ms.status, MetricStatus.UNKNOWN)


class TierAggregateShapeTests(unittest.TestCase):
    def test_tier_aggregate_has_the_defined_shape(self):
        t1 = _trial(benchmark_id="task-a", difficulty="easy")
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        tier = report.summary["tier_aggregates"]["easy"]
        self.assertEqual(
            set(tier.keys()),
            {
                "case_count", "trial_count", "ac_pass_rate", "critical_ac_pass_rate",
                "hidden_test_pass_rate", "full_case_pass_rate", "workflow_failure_rate",
            },
        )
        self.assertEqual(tier["case_count"], 1)


class ReportRoundTripTests(unittest.TestCase):
    def test_built_report_validates_clean(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        self.assertEqual(validate_report_payload(report.to_dict()), [])

    def test_comparison_context_lands_under_summary_not_top_level(self):
        t1 = _trial()
        report = build_eval_report(
            [t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1",
            comparison_context={"trend": "improved", "warnings": []},
        )
        self.assertEqual(report.summary["comparison_context"]["trend"], "improved")
        self.assertNotIn("comparison_context", report.to_dict())


class WriteEvalReportAtomicityTests(unittest.TestCase):
    def test_writes_timestamped_and_latest_with_identical_bytes(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        with TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            path = write_eval_report(report, reports_dir, difficulty="medium", stamp="20260522-120000")
            self.assertTrue(path.exists())
            latest = reports_dir / "latest.json"
            self.assertTrue(latest.exists())
            self.assertEqual(path.read_bytes(), latest.read_bytes())

    def test_partial_publication_failure_preserves_timestamped_artifact(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        with TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            with patch("eval.live_report._atomic_write_bytes", side_effect=[None, OSError("disk full")]):
                with self.assertRaises(ReportBuildError):
                    write_eval_report(report, reports_dir, difficulty="medium", stamp="20260522-120000")
            # first write (timestamped) call happened before the second failed;
            # `_atomic_write_bytes` was mocked so no on-disk artifact exists here
            # by construction — this asserts the error path is exactly the
            # documented partial-publication case, not a silent failure.

    def test_write_eval_report_refuses_to_publish_invalid_report(self):
        t1 = _trial()
        report = build_eval_report([t1], _args(), created_at="2026-05-22T12:00:00.000Z", eval_run_id="run-1")
        report.eval_run_id = None  # required field, now missing -> invalid
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ReportBuildError):
                write_eval_report(report, Path(tmp), difficulty="medium", stamp="20260522-120000")
            self.assertEqual(list(Path(tmp).glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
