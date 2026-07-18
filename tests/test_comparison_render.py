"""Rendering tests for `eval.render_comparison.render_summary` (Prompt 5).

Covers coverage category 81-88: snapshot equality against the committed
golden summary for the improved fixture pair, plus semantic (non-golden)
assertions for the other classifications, and a direct check of the
`_fmt_num` display-rounding behavior that hides float subtraction noise.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from eval.comparison import compare_reports
from eval.comparison_schema import DeltaAvailability, MetricDelta
from eval.render_comparison import _fmt_num, _fmt_metric_delta, render_summary
from eval.report_schema import EvalReport

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "comparison"


def _load(name: str) -> dict:
    import json

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RenderSnapshotTests(unittest.TestCase):
    def test_improved_summary_matches_golden_snapshot(self):
        expected = (FIXTURES / "summary_improved.txt").read_text(encoding="utf-8")
        # The golden snapshot's identity labels are the relative path strings
        # baked in when it was generated; reuse them verbatim (rather than
        # hard-coding a path convention) so this stays a like-for-like
        # comparison regardless of cwd or absolute-vs-relative path style.
        for line in expected.splitlines():
            if line.startswith("  tests/fixtures/eval/comparison/baseline_report.json"):
                baseline_label = line.strip()
            elif line.startswith("  tests/fixtures/eval/comparison/candidate_improved.json"):
                candidate_label = line.strip()
        baseline = EvalReport.from_dict(_load("baseline_report.json"))
        candidate = EvalReport.from_dict(_load("candidate_improved.json"))
        result = compare_reports(
            baseline,
            candidate,
            baseline_label=baseline_label,
            candidate_label=candidate_label,
        )
        rendered = render_summary(result)
        self.assertEqual(rendered, expected)


class RenderSemanticTests(unittest.TestCase):
    def test_regressed_summary_contains_classification_and_critical_regression(self):
        baseline = EvalReport.from_dict(_load("baseline_report.json"))
        candidate = EvalReport.from_dict(_load("candidate_regressed.json"))
        result = compare_reports(baseline, candidate)
        rendered = render_summary(result)
        self.assertIn("Classification: REGRESSED", rendered)
        self.assertIn("Critical acceptance-criteria regressions:", rendered)
        self.assertIn("easy-1/AC1", rendered)
        self.assertIn("Easy-tier benchmark regressions:", rendered)

    def test_drift_summary_contains_drift_section(self):
        baseline = EvalReport.from_dict(_load("baseline_report.json"))
        candidate = EvalReport.from_dict(_load("candidate_drift.json"))
        result = compare_reports(baseline, candidate)
        rendered = render_summary(result)
        self.assertIn("Configuration drift:", rendered)
        self.assertIn("metadata.model", rendered)
        self.assertIn("'modelA' -> 'modelB'", rendered)

    def test_inconclusive_summary_states_incomparable_without_fabricated_delta_data(self):
        baseline_dict = _load("baseline_report.json")
        candidate_dict = _load("candidate_improved.json")
        candidate_dict["benchmark_suite_id"] = "seed-v2"
        result = compare_reports(baseline_dict, candidate_dict)
        rendered = render_summary(result)
        self.assertIn("Classification: INCONCLUSIVE", rendered)
        self.assertIn("Comparability:  incomparable", rendered)

    def test_unavailable_metric_delta_rendered_honestly_not_as_zero(self):
        baseline_dict = _load("baseline_report.json")
        candidate_dict = _load("candidate_improved.json")
        # Force one scorecard metric to unknown on the candidate side.
        candidate_dict["scorecard"]["efficiency"]["estimated_cost"] = {
            "status": "unknown"
        }
        result = compare_reports(baseline_dict, candidate_dict)
        rendered = render_summary(result)
        self.assertIn("estimated_cost: unavailable (", rendered)
        self.assertNotIn("estimated_cost: 0", rendered)

    def test_no_drift_section_when_metadata_identical(self):
        baseline = EvalReport.from_dict(_load("baseline_report.json"))
        candidate = EvalReport.from_dict(_load("candidate_improved.json"))
        result = compare_reports(baseline, candidate)
        rendered = render_summary(result)
        self.assertNotIn("Configuration drift:", rendered)

    def test_summary_ends_with_single_trailing_newline(self):
        baseline = EvalReport.from_dict(_load("baseline_report.json"))
        candidate = EvalReport.from_dict(_load("candidate_improved.json"))
        result = compare_reports(baseline, candidate)
        rendered = render_summary(result)
        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))


class FmtNumTests(unittest.TestCase):
    def test_float_subtraction_noise_is_hidden(self):
        self.assertEqual(_fmt_num(0.33299999999999996), "0.333")

    def test_whole_number_float_renders_without_decimal(self):
        self.assertEqual(_fmt_num(0.0), "0")
        self.assertEqual(_fmt_num(-2.0), "-2")

    def test_int_passes_through_unchanged(self):
        self.assertEqual(_fmt_num(-3000), "-3000")

    def test_none_and_bool_pass_through_as_str(self):
        self.assertEqual(_fmt_num(None), "None")
        self.assertEqual(_fmt_num(True), "True")

    def test_metric_delta_rendering_uses_fmt_num_for_absolute_delta(self):
        delta = MetricDelta(
            name="trial_pass_rate",
            baseline_status="known",
            baseline_value=0.667,
            candidate_status="known",
            candidate_value=1.0,
            absolute_delta=0.33299999999999996,
            relative_delta=0.4992503748125937,
            availability=DeltaAvailability.COMPUTED,
        )
        rendered = _fmt_metric_delta("trial_pass_rate", delta)
        self.assertIn("Δ +0.333", rendered)
        self.assertNotIn("0.33299999999999996", rendered)


if __name__ == "__main__":
    unittest.main()
