import tempfile
import unittest
from pathlib import Path

from core.story_inputs import (
    count_acceptance_criteria,
    infer_manual_change_id,
    load_manual_story,
    normalize_acceptance_criteria,
    validate_manual_story,
)


class ManualStoryValidationTests(unittest.TestCase):
    def test_easy__validate_manual_story_accepts_text_acceptance_criteria(self):
        payload = validate_manual_story(
            {
                "title": "Manual story",
                "description": "desc",
                "acceptance_criteria": "- first\n- second",
            }
        )

        self.assertEqual(payload["title"], "Manual story")
        self.assertEqual(payload["acceptance_criteria"], "- first\n- second")

    def test_medium__validate_manual_story_rejects_missing_required_fields(self):
        with self.assertRaisesRegex(ValueError, "missing required field"):
            validate_manual_story({"title": "Only title"})


class ManualAcceptanceCriteriaNormalizationTests(unittest.TestCase):
    def test_easy__normalizes_numbered_acceptance_criteria_text(self):
        normalized = normalize_acceptance_criteria("1. first\n2. second")

        self.assertEqual(normalized, {"AC1": "first", "AC2": "second"})

    def test_easy__falls_back_to_single_acceptance_criterion_for_freeform_text(self):
        normalized = normalize_acceptance_criteria("Given a user can submit a story manually")

        self.assertEqual(normalized, {"AC1": "Given a user can submit a story manually"})

    def test_easy__counts_manual_acceptance_criteria_from_text(self):
        self.assertEqual(count_acceptance_criteria("- first\n- second\n- third"), 3)


class ManualChangeIdInferenceTests(unittest.TestCase):
    def test_easy__infers_wi_change_id_from_work_item_id(self):
        change_id = infer_manual_change_id(
            {
                "work_item_id": "123456",
                "title": "Manual story",
                "description": "desc",
                "acceptance_criteria": "- first",
            }
        )

        self.assertEqual(change_id, "WI-123456")

    def test_medium__sanitizes_explicit_manual_change_id(self):
        change_id = infer_manual_change_id(
            {
                "title": "Manual story",
                "description": "desc",
                "acceptance_criteria": "- first",
            },
            explicit_change_id="Manual Story / Alpha",
        )

        self.assertEqual(change_id, "Manual-Story-Alpha")


class ManualStoryFileLoadingTests(unittest.TestCase):
    def test_easy__load_manual_story_round_trips_json_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "manual_story.json"
            story_path.write_text(
                '{"title":"Manual story","description":"desc","acceptance_criteria":"- first\\n- second"}',
                encoding="utf-8",
            )

            payload = load_manual_story(str(story_path))

        self.assertEqual(payload["title"], "Manual story")
        self.assertEqual(payload["description"], "desc")


if __name__ == "__main__":
    unittest.main()
