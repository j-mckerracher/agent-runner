# Difficulty rubric for this file:
#   easy   = single-field presence or value equality on a pre-built registry
#            (e.g. asserting a count or a single name).
#   medium = exercising STORY_CHECK_BUILDERS via patch.dict, requires mock
#            setup and dispatch verification.
#   hard   = (none in this file)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval import story_checks


class StoryCheckRegistryTests(unittest.TestCase):
    def test_easy__eval_001_resolved_change_id_is_eval_001(self):
        resolved_change_id, _ = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-001", timeout=1)
        self.assertEqual(resolved_change_id, "EVAL-001")

    def test_easy__eval_001_registry_has_eight_checks(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-001", timeout=1)
        self.assertEqual(len(checks), 8)

    def test_easy__eval_001_first_check_is_badge_data_test_id(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-001", timeout=1)
        self.assertEqual(checks[0].name, "badge_data_test_id")

    def test_easy__eval_001_last_check_is_cypress_test_case(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-001", timeout=1)
        self.assertEqual(checks[-1].name, "cypress_test_case")

    def test_easy__eval_002_resolved_change_id_is_eval_002(self):
        resolved_change_id, _ = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-002", timeout=1)
        self.assertEqual(resolved_change_id, "EVAL-002")

    def test_easy__eval_002_registry_has_fourteen_checks(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-002", timeout=1)
        self.assertEqual(len(checks), 14)

    def test_easy__eval_002_first_check_is_summary_container_data_test_id(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-002", timeout=1)
        self.assertEqual(checks[0].name, "summary_container_data_test_id")

    def test_easy__eval_002_last_check_is_nx_build(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-002", timeout=1)
        self.assertEqual(checks[-1].name, "nx_build")

    def test_easy__eval_003_resolved_change_id_is_eval_003(self):
        resolved_change_id, _ = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-003", timeout=1)
        self.assertEqual(resolved_change_id, "EVAL-003")

    def test_easy__eval_003_registry_has_twenty_checks(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-003", timeout=1)
        self.assertEqual(len(checks), 20)

    def test_easy__eval_003_first_check_is_helper_method_exists(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-003", timeout=1)
        self.assertEqual(checks[0].name, "helper_method_exists")

    def test_easy__eval_003_last_check_is_nx_build(self):
        _, checks = story_checks.get_story_checks("/tmp/mono", change_id="EVAL-003", timeout=1)
        self.assertEqual(checks[-1].name, "nx_build")

    def test_medium__resolve_story_change_id_reads_change_id_from_story_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            resolved_change_id = story_checks.resolve_story_change_id(story_file=story_path)

        self.assertEqual(resolved_change_id, "EVAL-002")


class StoryCheckExecutionTests(unittest.TestCase):
    def test_medium__legacy_default_run_returns_eval_001_story(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("legacy_default", lambda: True)]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-001": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", timeout=1)

        self.assertEqual(result["story"], "EVAL-001")

    def test_medium__legacy_default_run_returns_full_score(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("legacy_default", lambda: True)]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-001": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", timeout=1)

        self.assertEqual(result["score"], 100)

    def test_medium__legacy_default_run_first_check_name_is_legacy_default(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("legacy_default", lambda: True)]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-001": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", timeout=1)

        self.assertEqual(result["checks"][0]["name"], "legacy_default")

    def test_medium__story_file_dispatch_resolves_to_change_id_in_file(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("story_file_dispatch", lambda: True)]

        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-002": builder}, clear=False):
                result = story_checks.run_story_checks("/tmp/mono", story_file=story_path, timeout=1)

        self.assertEqual(result["story"], "EVAL-002")

    def test_medium__story_file_dispatch_passing_count_equals_one(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("story_file_dispatch", lambda: True)]

        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-002": builder}, clear=False):
                result = story_checks.run_story_checks("/tmp/mono", story_file=story_path, timeout=1)

        self.assertEqual(result["passing"], 1)

    def test_medium__story_file_dispatch_total_count_equals_one(self):
        builder = lambda mono_root, timeout: [story_checks.CheckDefinition("story_file_dispatch", lambda: True)]

        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "EVAL-002.json"
            story_path.write_text(json.dumps({"change_id": "EVAL-002"}), encoding="utf-8")

            with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-002": builder}, clear=False):
                result = story_checks.run_story_checks("/tmp/mono", story_file=story_path, timeout=1)

        self.assertEqual(result["total"], 1)

    def test_medium__partial_results_story_id_matches_change_id(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["story"], "EVAL-999")

    def test_medium__partial_results_passing_count_is_one(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["passing"], 1)

    def test_medium__partial_results_total_count_is_two(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["total"], 2)

    def test_medium__partial_results_score_is_fifty(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["score"], 50)

    def test_medium__partial_results_second_check_name_is_fail_check(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertEqual(result["checks"][1]["name"], "fail_check")

    def test_medium__partial_results_second_check_passed_is_false(self):
        builder = lambda mono_root, timeout: [
            story_checks.CheckDefinition("pass_check", lambda: True),
            story_checks.CheckDefinition("fail_check", lambda: False),
        ]

        with patch.dict(story_checks.STORY_CHECK_BUILDERS, {"EVAL-999": builder}, clear=False):
            result = story_checks.run_story_checks("/tmp/mono", change_id="EVAL-999", timeout=1)

        self.assertFalse(result["checks"][1]["passed"])

    def test_easy__get_story_checks_raises_value_error_for_unknown_story(self):
        with self.assertRaisesRegex(ValueError, "Unsupported eval story"):
            story_checks.get_story_checks("/tmp/mono", change_id="EVAL-404", timeout=1)


if __name__ == "__main__":
    unittest.main()
