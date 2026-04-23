import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval import story_checks


class StoryCheckRegistryTests(unittest.TestCase):
    def test_eval_001_registry_shape_is_preserved(self):
        resolved_change_id, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-001", timeout=1)

        self.assertEqual(resolved_change_id, "EVAL-001")
        self.assertEqual(len(checks), 8)
        self.assertEqual(checks[0].name, "badge_data_test_id")
        self.assertEqual(checks[-1].name, "cypress_test_case")

    def test_eval_002_registry_is_available(self):
        resolved_change_id, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-002", timeout=1)

        self.assertEqual(resolved_change_id, "EVAL-002")
        self.assertEqual(len(checks), 14)
        self.assertEqual(checks[0].name, "summary_container_data_test_id")
        self.assertEqual(checks[-1].name, "nx_build")

    def test_eval_003_registry_is_available(self):
        resolved_change_id, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-003", timeout=1)

        self.assertEqual(resolved_change_id, "EVAL-003")
        self.assertEqual(len(checks), 20)
        self.assertEqual(checks[0].name, "helper_method_exists")
        self.assertEqual(checks[-1].name, "nx_build")

    def test_resolve_story_change_id_from_story_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            resolved_change_id = story_checks.resolve_story_change_id(story_file=story_path)

        self.assertEqual(resolved_change_id, "EVAL-002")


class StoryCheckExecutionTests(unittest.TestCase):
    def test_run_story_checks_defaults_to_eval_001_for_legacy_calls(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("legacy_default", lambda: True)]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-001": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", timeout=1)

        self.assertEqual(result["story"], "EVAL-001")
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["checks"][0]["name"], "legacy_default")

    def test_run_story_checks_dispatches_by_story_file(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("story_file_dispatch", lambda: True)]

        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-002": builder}, clear=False):
                result = story_checks.run_story_checks("/tmp/mono", story_file=story_path, timeout=1)

        self.assertEqual(result["story"], "EVAL-002")
        self.assertEqual(result["passing"], 1)
        self.assertEqual(result["total"], 1)

    def test_run_story_checks_scores_partial_results(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["story"], "EVAL-999")
        self.assertEqual(result["passing"], 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["checks"][1]["name"], "fail_check")
        self.assertFalse(result["checks"][1]["passed"])

    def test_get_story_checks_rejects_unknown_story(self):
        with self.assertRaisesRegex(ValueError, "Unsupported eval story"):
            story_checks.get_story_checks("/tmp/mono", change_id="EVAL-404", timeout=1)


if __name__ == "__main__":
    unittest.main()

