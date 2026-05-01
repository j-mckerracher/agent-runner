import unittest

from eval.metrics import check_score_results
from eval.models import CheckResult
from eval.scoring import (
    score_attempted_rate,
    score_tier_high,
    score_tier_low,
    score_tier_medium,
    score_weighted_composite,
    summarize_scores,
)


class EvalScoringTests(unittest.TestCase):
    def test_composite_math_and_attempted_rate(self):
        results = [
            CheckResult("low-pass", True, difficulty="low"),
            CheckResult("medium-fail", False, attempted=False, difficulty="medium"),
            CheckResult("high-pass", True, difficulty="high"),
        ]

        self.assertEqual(score_tier_low(results), 1.0)
        self.assertEqual(score_tier_medium(results), 0.0)
        self.assertEqual(score_tier_high(results), 1.0)
        self.assertAlmostEqual(score_weighted_composite(results), 4.0 / 6.0)
        self.assertAlmostEqual(score_attempted_rate(results), 2.0 / 3.0)

        summary = summarize_scores(results)
        self.assertAlmostEqual(summary.weighted_composite, 4.0 / 6.0)
        self.assertAlmostEqual(summary.attempted_rate, 2.0 / 3.0)

    def test_no_high_checks_is_zero_not_error(self):
        results = [CheckResult("low-pass", True, difficulty="low")]

        self.assertEqual(score_tier_high(results), 0.0)
        self.assertEqual(score_attempted_rate([]), 0.0)
        self.assertEqual(score_weighted_composite([]), 0.0)

    def test_metric_name_format_and_metadata(self):
        result = CheckResult(
            "contains_title",
            False,
            mechanism="contains",
            subject="agent_output",
            difficulty="high",
            failure_reason="ASSERTION_MISS",
            message="missing title",
            metadata={"suite_tier": "stale", "regression": False},
        )

        metric = check_score_results([result], suite_tier="hard", regression=True)[0]

        self.assertEqual(metric.name, "contains_title_agent_output")
        self.assertNotIn("high", metric.name)
        self.assertEqual(metric.value, 0.0)
        self.assertEqual(metric.metadata["difficulty"], "high")
        self.assertEqual(metric.metadata["mechanism"], "contains")
        self.assertEqual(metric.metadata["failure"], "ASSERTION_MISS")
        self.assertEqual(metric.metadata["suite_tier"], "hard")
        self.assertTrue(metric.metadata["regression"])


if __name__ == "__main__":
    unittest.main()
