import unittest

from eval.check_helpers import contains_check, matches_check, run_check
from eval.models import (
    AcceptanceCriterion,
    CheckDefinition,
    CheckResult,
    EvalStory,
)
from eval.scoring import summarize_scores


class EvalModelTests(unittest.TestCase):
    def test_check_definition_round_trips_to_dict(self):
        definition = CheckDefinition(
            id="docs_contains_title",
            label="Docs mention title",
            mechanism="contains",
            subject="agent_output",
            expected="Example",
            difficulty="low",
        )

        round_trip = CheckDefinition.from_dict(definition.to_dict())

        self.assertEqual(round_trip, definition)

    def test_contains_definition_requires_expected_text(self):
        with self.assertRaisesRegex(ValueError, "expected"):
            CheckDefinition(
                id="bad",
                label="Bad",
                mechanism="contains",
                subject="agent_output",
            )

    def test_eval_story_acceptance_criteria_round_trip(self):
        story = EvalStory(
            story_id="story-001",
            title="Example story",
            description="Implement an example",
            suite_tier="easy",
            acceptance_criteria=[
                AcceptanceCriterion(
                    ac_id="AC1",
                    text="Output mentions Example",
                    tier="easy",
                    check=contains_check(
                        "output_mentions_example",
                        "Output mentions Example",
                        "agent_output",
                        "Example",
                    ),
                )
            ],
        )

        loaded = EvalStory.from_dict(story.to_dict())

        self.assertEqual(loaded.story_id, "story-001")
        self.assertEqual(loaded.acceptance_criteria[0].check.id, "output_mentions_example")

    def test_weighted_scoring_uses_difficulty_weights(self):
        summary = summarize_scores(
            [
                CheckResult(check_id="low", passed=True, difficulty="low"),
                CheckResult(check_id="high", passed=False, difficulty="high"),
            ]
        )

        self.assertEqual(summary.total_checks, 2)
        self.assertAlmostEqual(summary.weighted_composite, 0.25)

    def test_contains_and_matches_helpers_execute_against_agent_output(self):
        contains = contains_check("contains", "Contains", "agent_output", "needle")
        matches = matches_check("matches", "Matches", "agent_output", r"need[a-z]+")

        self.assertTrue(run_check(contains, "haystack needle").passed)
        self.assertTrue(run_check(matches, "haystack needle").passed)


if __name__ == "__main__":
    unittest.main()
