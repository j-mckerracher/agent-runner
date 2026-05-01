import sys
import unittest

from eval.check_helpers import command_check, contains_check, matches_check
from eval.models import AcceptanceCriterion, EvalStory
from eval.scoring import assign_check_difficulty, suggested_difficulty_for_check
from eval.story_checks import run_story_checks


def story_with_checks(*checks):
    return EvalStory(
        story_id="story-001",
        title="Story 001",
        description="A test story",
        acceptance_criteria=[
            AcceptanceCriterion(ac_id=f"AC{index}", text=check.label, tier="easy", check=check)
            for index, check in enumerate(checks, start=1)
        ],
    )


class CheckHelperTests(unittest.TestCase):
    def test_contains_factory_sets_expected_and_metadata(self):
        check = contains_check(
            "contains-title",
            "Contains title",
            "agent_output",
            "needle",
            metadata={"source": "test"},
        )

        self.assertEqual(check.mechanism, "contains")
        self.assertEqual(check.expected, "needle")
        self.assertEqual(check.metadata["source"], "test")

    def test_matches_factory_sets_pattern(self):
        check = matches_check("matches-title", "Matches title", "agent_output", r"need[a-z]+")

        self.assertEqual(check.mechanism, "matches")
        self.assertEqual(check.expected, r"need[a-z]+")

    def test_command_factory_sets_command(self):
        check = command_check("cmd", "Command", "build", [sys.executable, "--version"])

        self.assertEqual(check.mechanism, "command")
        self.assertEqual(check.command, [sys.executable, "--version"])


class DifficultyAssignmentTests(unittest.TestCase):
    def test_two_signal_difficulty_assigns_low_medium_high(self):
        low = contains_check("low", "Low", "file", "needle")
        medium = matches_check("medium", "Medium", "agent_output", r"needle")
        high = command_check("high", "High", "build", [sys.executable, "--version"])

        self.assertEqual(suggested_difficulty_for_check(low), "low")
        self.assertEqual(suggested_difficulty_for_check(medium), "medium")
        self.assertEqual(suggested_difficulty_for_check(high), "high")

    def test_manual_override_wins_and_preserves_suggested_difficulty(self):
        check = contains_check("manual", "Manual", "build", "needle")

        assigned = assign_check_difficulty(check, {"manual": "high"})

        self.assertEqual(assigned.difficulty, "high")
        self.assertEqual(assigned.suggested_difficulty, "medium")

    def test_definition_difficulty_wins_and_preserves_suggested_difficulty(self):
        check = command_check(
            "manual-definition",
            "Manual definition",
            "build",
            [sys.executable, "--version"],
            difficulty="low",
        )

        assigned = assign_check_difficulty(check)

        self.assertEqual(assigned.difficulty, "low")
        self.assertEqual(assigned.suggested_difficulty, "high")


class StoryCheckExecutionTests(unittest.TestCase):
    def test_run_story_checks_passes_contains_and_matches(self):
        story = story_with_checks(
            contains_check("contains", "Contains", "agent_output", "needle"),
            matches_check("matches", "Matches", "agent_output", r"need[a-z]+"),
        )

        results = run_story_checks("story-001", "haystack needle", suite_story=story)

        self.assertTrue(all(result.passed for result in results))

    def test_direct_story_with_dict_criteria_runs_checks(self):
        story = EvalStory(
            story_id="story-001",
            title="Story 001",
            description="A test story",
            acceptance_criteria=[
                {
                    "ac_id": "AC1",
                    "text": "Contains needle",
                    "tier": "easy",
                    "check": contains_check("contains", "Contains", "agent_output", "needle").to_dict(),
                }
            ],
        )

        result = run_story_checks("story-001", "haystack needle", suite_story=story)[0]

        self.assertTrue(result.passed)

    def test_failed_assertion_has_failure_reason(self):
        story = story_with_checks(contains_check("missing", "Missing", "agent_output", "needle"))

        result = run_story_checks("story-001", "haystack", suite_story=story)[0]

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_reason, "ASSERTION_MISS")

    def test_empty_output_marks_all_checks_no_attempt_and_not_attempted(self):
        story = story_with_checks(
            contains_check("contains", "Contains", "agent_output", "needle"),
            command_check("command", "Command", "build", [sys.executable, "--version"]),
        )

        results = run_story_checks("story-001", "", suite_story=story)

        self.assertEqual([result.failure_reason for result in results], ["NO_ATTEMPT", "NO_ATTEMPT"])
        self.assertEqual([result.attempted for result in results], [False, False])

    def test_declined_output_marks_no_attempt(self):
        story = story_with_checks(contains_check("contains", "Contains", "agent_output", "needle"))

        for output in (
            "I cannot complete this request.",
            "Unfortunately, I cannot complete this request.",
            "Unfortunately,I cannot complete this request.",
        ):
            with self.subTest(output=output):
                result = run_story_checks("story-001", output, suite_story=story)[0]

                self.assertEqual(result.failure_reason, "NO_ATTEMPT")
                self.assertFalse(result.attempted)

    def test_decline_phrase_with_implementation_is_attempted(self):
        story = story_with_checks(contains_check("contains", "Contains", "agent_output", "needle"))

        result = run_story_checks(
            "story-001",
            "I decline to use the risky approach, but implemented the needle feature.",
            suite_story=story,
        )[0]

        self.assertTrue(result.attempted)
        self.assertTrue(result.passed)

    def test_decline_phrase_about_rejected_approach_is_attempted(self):
        story = story_with_checks(contains_check("contains", "Contains", "agent_output", "needle"))

        result = run_story_checks(
            "story-001",
            "I decline to use the risky approach; the safer answer includes needle.",
            suite_story=story,
        )[0]

        self.assertTrue(result.attempted)
        self.assertTrue(result.passed)

    def test_non_zero_command_is_build_error(self):
        story = story_with_checks(
            command_check(
                "command-fails",
                "Command fails",
                "build",
                [sys.executable, "-c", "import sys; sys.exit(7)"],
            )
        )

        result = run_story_checks("story-001", "implemented", suite_story=story)[0]

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_reason, "BUILD_ERROR")

    def test_command_timeout_is_timeout(self):
        story = story_with_checks(
            command_check(
                "command-timeout",
                "Command timeout",
                "execute",
                [sys.executable, "-c", "import time; time.sleep(2)"],
            )
        )

        result = run_story_checks("story-001", "implemented", suite_story=story, timeout=0.1)[0]

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_reason, "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
