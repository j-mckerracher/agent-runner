import json
import tempfile
import unittest
from pathlib import Path

from workflow_inputs import (
    DEFAULT_TEST_STORY_FILE,
    infer_change_id_from_ado_url,
    load_story_fixture,
    resolve_workflow_input,
)


class WorkflowInputsTests(unittest.TestCase):
    def test_load_story_fixture_accepts_list_acceptance_criteria(self):
        fixture = load_story_fixture(str(DEFAULT_TEST_STORY_FILE))

        self.assertEqual(fixture["change_id"], "TEST-AC-001")
        self.assertIsInstance(fixture["acceptance_criteria"], list)
        self.assertGreater(len(fixture["acceptance_criteria"]), 0)

    def test_load_story_fixture_accepts_map_acceptance_criteria(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-002",
                        "title": "Fixture with keyed ACs",
                        "description": "Synthetic story",
                        "acceptance_criteria": {
                            "AC1": "first criterion",
                            "AC2": "second criterion",
                        },
                    }
                ),
                encoding="utf-8",
            )

            fixture = load_story_fixture(str(path))

        self.assertEqual(fixture["acceptance_criteria"]["AC1"], "first criterion")

    def test_load_story_fixture_rejects_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(json.dumps({"title": "Missing fields"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required field"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_invalid_acceptance_criteria_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-003",
                        "title": "Bad fixture",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["valid", ""],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_infer_change_id_from_ado_url(self):
        self.assertEqual(
            infer_change_id_from_ado_url(
                "https://dev.azure.com/example/project/_workitems/edit/123456"
            ),
            "WI-123456",
        )
        self.assertEqual(infer_change_id_from_ado_url("123456"), "WI-123456")
        self.assertIsNone(infer_change_id_from_ado_url("not-a-work-item"))

    def test_resolve_workflow_input_defaults_to_bundled_synthetic_story(self):
        workflow_input = resolve_workflow_input(repo="/tmp/repo")

        self.assertEqual(workflow_input.intake_mode, "synthetic")
        self.assertEqual(Path(workflow_input.intake_source), DEFAULT_TEST_STORY_FILE)
        self.assertEqual(workflow_input.change_id, "TEST-AC-001")
        self.assertEqual(workflow_input.repo, "/tmp/repo")

    def test_resolve_workflow_input_detects_change_id_mismatch_for_fixture(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            resolve_workflow_input(
                repo="/tmp/repo",
                story_file=str(DEFAULT_TEST_STORY_FILE),
                change_id="DIFFERENT-ID",
            )

    def test_resolve_workflow_input_accepts_explicit_change_id_for_fixture_without_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "title": "Fixture without change id",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["criterion"],
                    }
                ),
                encoding="utf-8",
            )

            workflow_input = resolve_workflow_input(
                repo="/tmp/repo",
                story_file=str(path),
                change_id="TEST-AC-777",
            )

        self.assertEqual(workflow_input.change_id, "TEST-AC-777")
        self.assertEqual(workflow_input.intake_mode, "synthetic")

    def test_resolve_workflow_input_requires_change_id_when_fixture_omits_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "title": "Fixture without change id",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["criterion"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must include change_id"):
                resolve_workflow_input(repo="/tmp/repo", story_file=str(path))

    def test_resolve_workflow_input_supports_ado_mode(self):
        workflow_input = resolve_workflow_input(
            repo="/tmp/repo",
            ado_url="https://dev.azure.com/example/project/_workitems/edit/555",
        )

        self.assertEqual(workflow_input.intake_mode, "ado")
        self.assertEqual(workflow_input.change_id, "WI-555")
        self.assertIn("_workitems/edit/555", workflow_input.intake_source)

    def test_load_story_fixture_rejects_empty_acceptance_criteria_list(self):
        """Edge case: acceptance_criteria is an empty list"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-004",
                        "title": "Fixture with empty list",
                        "description": "Synthetic story",
                        "acceptance_criteria": [],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_empty_acceptance_criteria_dict(self):
        """Edge case: acceptance_criteria is an empty dict"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-005",
                        "title": "Fixture with empty dict",
                        "description": "Synthetic story",
                        "acceptance_criteria": {},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_whitespace_only_strings_in_list(self):
        """Edge case: acceptance_criteria list contains whitespace-only strings"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-006",
                        "title": "Fixture with whitespace",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["valid", "   ", "\t", "\n"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_none_values_in_list(self):
        """Edge case: acceptance_criteria list contains None values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-007",
                        "title": "Fixture with None",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["valid", None],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_non_string_values_in_list(self):
        """Edge case: acceptance_criteria list contains non-string values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-008",
                        "title": "Fixture with int",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["valid", 42],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_whitespace_only_dict_keys(self):
        """Edge case: acceptance_criteria dict contains whitespace-only keys"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-009",
                        "title": "Fixture with whitespace key",
                        "description": "Synthetic story",
                        "acceptance_criteria": {"   ": "value", "AC1": "valid"},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_whitespace_only_dict_values(self):
        """Edge case: acceptance_criteria dict contains whitespace-only values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-010",
                        "title": "Fixture with whitespace value",
                        "description": "Synthetic story",
                        "acceptance_criteria": {"AC1": "   ", "AC2": "valid"},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_non_string_dict_values(self):
        """Edge case: acceptance_criteria dict contains non-string values"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-011",
                        "title": "Fixture with int value",
                        "description": "Synthetic story",
                        "acceptance_criteria": {"AC1": 42, "AC2": "valid"},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptance_criteria"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_invalid_json_structure(self):
        """Edge case: fixture is valid JSON but not a dict"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text('["not", "a", "dict"]', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_missing_title_field(self):
        """Edge case: acceptance_criteria is valid but title is missing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-012",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["criterion"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing required field"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_missing_description_field(self):
        """Edge case: acceptance_criteria is valid but description is missing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-013",
                        "title": "Story title",
                        "acceptance_criteria": ["criterion"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing required field"):
                load_story_fixture(str(path))

    def test_load_story_fixture_rejects_empty_title_field(self):
        """Edge case: title is present but empty string"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "change_id": "TEST-AC-014",
                        "title": "",
                        "description": "Synthetic story",
                        "acceptance_criteria": ["criterion"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing required field"):
                load_story_fixture(str(path))


if __name__ == "__main__":
    unittest.main()

