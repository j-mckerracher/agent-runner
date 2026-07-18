"""Tests for `eval/render_report.py::render_eval_summary`.

Reuses the canonical, already-valid v0.2 report fixtures under
`tests/fixtures/eval/comparison/` (the same reports `test_eval_comparison.py`
and `test_v02_end_to_end.py` compare against) rather than hand-building a new
`EvalReport` -- this keeps the rendering tests grounded in the same
schema-valid shape the rest of the suite already trusts.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from eval.render_report import render_eval_summary
from eval.report_schema import EvalReport, Metric, MetricStatus

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "comparison"


def _load(name: str) -> EvalReport:
    return EvalReport.from_json((FIXTURES / name).read_text(encoding="utf-8"))


class RenderEvalSummaryTests(unittest.TestCase):
    def test_easy__includes_identity_fields(self):
        report = _load("baseline_report.json")
        text = render_eval_summary(report)
        self.assertIn(f"schema v{report.report_schema_version}", text)
        self.assertIn(f"eval_run_id:        {report.eval_run_id}", text)
        self.assertIn(f"candidate_version:  {report.candidate_version}", text)
        self.assertIn(f"benchmark_suite_id: {report.benchmark_suite_id}", text)

    def test_easy__includes_report_path_only_when_given(self):
        report = _load("baseline_report.json")
        without_path = render_eval_summary(report)
        self.assertNotIn("report_path:", without_path)

        with_path = render_eval_summary(report, report_path="/tmp/latest.json")
        self.assertIn("report_path:        /tmp/latest.json", with_path)

    def test_easy__baseline_version_unavailable_when_none(self):
        report = _load("baseline_report.json")
        report.baseline_version = None
        text = render_eval_summary(report)
        self.assertIn("baseline_version:   unavailable", text)

    def test_medium__case_and_trial_counts_are_plain_counts_not_policy(self):
        report = _load("candidate_regressed.json")
        text = render_eval_summary(report)
        cases = report.benchmark_case_results
        trials = [t for case in cases for t in case.trial_results]
        self.assertIn(f"benchmark cases: {len(cases)} (", text)
        self.assertIn(f"trials:          {len(trials)}", text)

    def test_medium__known_metric_rendered_as_percentage(self):
        report = _load("baseline_report.json")
        report.scorecard.capability.ac_pass_rate = Metric.known(0.75)
        text = render_eval_summary(report)
        self.assertIn("ac_pass_rate:            75.0%", text)

    def test_medium__unknown_metric_rendered_as_unavailable_with_reason_never_zero(self):
        report = _load("baseline_report.json")
        report.scorecard.capability.ac_pass_rate = Metric.unknown("acceptance criteria not instrumented")
        text = render_eval_summary(report)
        self.assertIn(
            "ac_pass_rate:            unavailable (unknown: acceptance criteria not instrumented)",
            text,
        )
        self.assertNotIn("ac_pass_rate:            0", text)
        self.assertNotIn("ac_pass_rate:            0.0%", text)

    def test_medium__not_collected_metric_rendered_as_unavailable_never_coerced_to_zero(self):
        report = _load("baseline_report.json")
        report.scorecard.efficiency.token_usage = Metric.not_collected("token accounting disabled for this run")
        text = render_eval_summary(report)
        self.assertIn(
            "token_usage:             unavailable (not_collected: token accounting disabled for this run)",
            text,
        )
        self.assertNotIn("token_usage:             0", text)

    def test_medium__not_applicable_metric_rendered_with_reason(self):
        report = _load("baseline_report.json")
        report.scorecard.reliability.flaky_case_count = Metric.not_applicable("single-trial run")
        text = render_eval_summary(report)
        self.assertIn(
            "flaky_case_count:        unavailable (not_applicable: single-trial run)",
            text,
        )

    def test_medium__missing_reason_still_renders_without_crashing(self):
        report = _load("baseline_report.json")
        report.scorecard.efficiency.estimated_cost = Metric(status=MetricStatus.UNKNOWN, unavailable_reason=None)
        text = render_eval_summary(report)
        self.assertIn("estimated_cost:          unavailable (unknown: no reason given)", text)

    def test_medium__non_numeric_known_value_is_not_formatted_as_percentage(self):
        report = _load("baseline_report.json")
        report.scorecard.traceability.trace_present = Metric.known(True)
        text = render_eval_summary(report)
        self.assertIn("trace_present:           True", text)

    def test_medium__trace_references_listed_when_present(self):
        report = _load("baseline_report.json")
        text = render_eval_summary(report)
        if report.trace_references:
            self.assertIn("Trace references:", text)
            for ref in report.trace_references:
                self.assertIn(ref.uri, text)
        else:
            self.assertIn("Trace references: (none)", text)

    def test_medium__trace_references_absent_renders_none_marker(self):
        report = _load("baseline_report.json")
        report.trace_references = []
        text = render_eval_summary(report)
        self.assertIn("Trace references: (none)", text)

    def test_medium__artifact_references_listed_when_present(self):
        from eval.report_schema import Reference

        report = _load("baseline_report.json")
        report.artifact_references = [Reference(ref_id="art-1", uri="/tmp/artifact.txt", kind="artifact")]
        text = render_eval_summary(report)
        self.assertIn("Artifact references:", text)
        self.assertIn("art-1", text)
        self.assertIn("/tmp/artifact.txt", text)

    def test_medium__non_passing_cases_are_listed_with_difficulty_and_status(self):
        report = _load("candidate_regressed.json")
        text = render_eval_summary(report)
        from eval.report_schema import CaseStatus

        failed = [c for c in report.benchmark_case_results if c.status != CaseStatus.PASSED]
        if failed:
            self.assertIn("Non-passing cases:", text)
            for case in failed:
                self.assertIn(f"{case.benchmark_id} (difficulty={case.difficulty}): {case.status.value}", text)

    def test_medium__all_passing_report_has_no_non_passing_section(self):
        report = _load("baseline_report.json")
        from eval.report_schema import CaseStatus

        for case in report.benchmark_case_results:
            case.status = CaseStatus.PASSED
        text = render_eval_summary(report)
        self.assertNotIn("Non-passing cases:", text)

    def test_medium__never_performs_classification_or_comparison_wording(self):
        # `render_eval_summary` reads only the single report it is given --
        # it must never mention comparison-only vocabulary that belongs to
        # `eval/render_comparison.py`'s classification/comparability output.
        report = _load("candidate_regressed.json")
        text = render_eval_summary(report)
        for forbidden in ("Classification:", "Comparability:", "Regressed benchmarks:", "Regressed acceptance criteria:"):
            self.assertNotIn(forbidden, text)

    def test_medium__output_is_stable_and_ends_with_single_trailing_newline(self):
        report = _load("baseline_report.json")
        text = render_eval_summary(report)
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))
        # Rendering the same report twice produces identical text -- no
        # hidden timestamp/random-id drift introduced by the renderer itself.
        self.assertEqual(text, render_eval_summary(report))

    def test_medium__all_scorecard_metric_lines_are_rendered(self):
        report = _load("baseline_report.json")
        text = render_eval_summary(report)
        expected_labels = [
            "ac_pass_rate:", "critical_ac_pass_rate:", "hidden_test_pass_rate:", "full_benchmark_pass_rate:",
            "trial_pass_rate:", "variance:", "flaky_case_count:", "workflow_failure_rate:",
            "wall_clock_duration_ms:", "agent_invocation_count:", "loop_iteration_count:", "token_usage:", "estimated_cost:",
            "trace_present:", "test_output_present:", "final_artifact_refs_present:", "final_diff_ref_present:", "prompt_hashes_present:",
        ]
        for label in expected_labels:
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
