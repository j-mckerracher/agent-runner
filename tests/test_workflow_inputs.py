# Difficulty rubric for this file:
#   easy   = single-field assertion on the return value of load_story_fixture
#            or resolve_workflow_input.
#   medium = ValueError-raising error path tests where a malformed fixture or
#            input must produce a specific message.
#   hard   = (none in this file)

import json
import tempfile
import unittest
from pathlib import Path

from core.workflow_inputs import (
    DEFAULT_TEST_STORY_FILE,
    infer_change_id_from_ado_url,
    load_story_fixture,
    resolve_workflow_input,
)


def _write_fixture(path: Path, payload: dict | list | str) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def _make_repo_dir() -> tempfile.TemporaryDirectory[str]:
    repo_dir = tempfile.TemporaryDirectory()
    Path(repo_dir.name).mkdir(parents=True, exist_ok=True)
    return repo_dir


class LoadStoryFixtureBundledTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_story_fixture(str(DEFAULT_TEST_STORY_FILE))

    def test_easy__bundled_fixture_change_id_equals_test_ac_001(self):
        self.assertEqual(self.fixture["change_id"], "TEST-AC-001")

    def test_easy__bundled_fixture_acceptance_criteria_is_a_list(self):
        self.assertIsInstance(self.fixture["acceptance_criteria"], list)

    def test_easy__bundled_fixture_acceptance_criteria_is_non_empty(self):
        self.assertGreater(len(self.fixture["acceptance_criteria"]), 0)


class LoadStoryFixtureMapAcceptanceCriteriaTests(unittest.TestCase):
    def test_easy__keyed_acceptance_criteria_ac1_value_round_trips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            _write_fixture(
                path,
                {
                    "change_id": "TEST-AC-002",
                    "title": "Fixture with keyed ACs",
                    "description": "Synthetic story",
                    "acceptance_criteria": {
                        "AC1": "first criterion",
                        "AC2": "second criterion",
                    },
                },
            )
            fixture = load_story_fixture(str(path))
        self.assertEqual(fixture["acceptance_criteria"]["AC1"], "first criterion")


class LoadStoryFixtureRejectionTests(unittest.TestCase):
    def _expect_value_error(self, payload, pattern):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "story.json"
            _write_fixture(path, payload)
            with self.assertRaisesRegex(ValueError, pattern):
                load_story_fixture(str(path))

    def test_medium__rejects_fixture_missing_required_fields(self):
        self._expect_value_error({"title": "Missing fields"}, "missing required field")

    def test_medium__rejects_invalid_acceptance_criteria_shape_with_empty_string(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-003",
                "title": "Bad fixture",
                "description": "Synthetic story",
                "acceptance_criteria": ["valid", ""],
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_empty_acceptance_criteria_list(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-004",
                "title": "Fixture with empty list",
                "description": "Synthetic story",
                "acceptance_criteria": [],
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_empty_acceptance_criteria_dict(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-005",
                "title": "Fixture with empty dict",
                "description": "Synthetic story",
                "acceptance_criteria": {},
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_whitespace_only_acceptance_criteria_strings(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-006",
                "title": "Fixture with whitespace",
                "description": "Synthetic story",
                "acceptance_criteria": ["valid", "   ", "\t", "\n"],
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_none_value_in_acceptance_criteria_list(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-007",
                "title": "Fixture with None",
                "description": "Synthetic story",
                "acceptance_criteria": ["valid", None],
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_non_string_value_in_acceptance_criteria_list(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-008",
                "title": "Fixture with int",
                "description": "Synthetic story",
                "acceptance_criteria": ["valid", 42],
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_whitespace_only_dict_keys(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-009",
                "title": "Fixture with whitespace key",
                "description": "Synthetic story",
                "acceptance_criteria": {"   ": "value", "AC1": "valid"},
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_whitespace_only_dict_values(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-010",
                "title": "Fixture with whitespace value",
                "description": "Synthetic story",
                "acceptance_criteria": {"AC1": "   ", "AC2": "valid"},
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_non_string_dict_values(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-011",
                "title": "Fixture with int value",
                "description": "Synthetic story",
                "acceptance_criteria": {"AC1": 42, "AC2": "valid"},
            },
            "acceptance_criteria",
        )

    def test_medium__rejects_top_level_json_array(self):
        self._expect_value_error('["not", "a", "dict"]', "must be a JSON object")

    def test_medium__rejects_fixture_missing_title_field(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-012",
                "description": "Synthetic story",
                "acceptance_criteria": ["criterion"],
            },
            "missing required field",
        )

    def test_medium__rejects_fixture_missing_description_field(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-013",
                "title": "Story title",
                "acceptance_criteria": ["criterion"],
            },
            "missing required field",
        )

    def test_medium__rejects_empty_title_field(self):
        self._expect_value_error(
            {
                "change_id": "TEST-AC-014",
                "title": "",
                "description": "Synthetic story",
                "acceptance_criteria": ["criterion"],
            },
            "missing required field",
        )


class InferChangeIdFromAdoUrlTests(unittest.TestCase):
    def test_easy__full_ado_url_resolves_to_numeric_id(self):
        self.assertEqual(
            infer_change_id_from_ado_url(
                "https://dev.azure.com/example/project/_workitems/edit/123456"
            ),
            "123456",
        )

    def test_easy__bare_numeric_string_resolves_to_numeric_id(self):
        self.assertEqual(infer_change_id_from_ado_url("123456"), "123456")

    def test_easy__non_work_item_string_resolves_to_none(self):
        self.assertIsNone(infer_change_id_from_ado_url("not-a-work-item"))


class ResolveWorkflowInputBundledFixtureTests(unittest.TestCase):
    def setUp(self):
        self._repo = _make_repo_dir()
        self.addCleanup(self._repo.cleanup)
        self.workflow_input = resolve_workflow_input(repo=self._repo.name)

    def test_easy__default_workflow_input_intake_mode_is_synthetic(self):
        self.assertEqual(self.workflow_input.intake_mode, "synthetic")

    def test_easy__default_workflow_input_intake_source_is_default_test_story(self):
        self.assertEqual(Path(self.workflow_input.intake_source), DEFAULT_TEST_STORY_FILE)

    def test_easy__default_workflow_input_change_id_is_test_ac_001(self):
        self.assertEqual(self.workflow_input.change_id, "TEST-AC-001")

    def test_easy__default_workflow_input_repo_is_passed_through(self):
        self.assertEqual(self.workflow_input.repo, str(Path(self._repo.name).resolve()))

    def test_easy__default_workflow_input_branch_description_source_uses_story_title(self):
        self.assertEqual(
            self.workflow_input.branch_description_source,
            "Synthetic local fixture smoke test",
        )


class ResolveWorkflowInputErrorPathTests(unittest.TestCase):
    def test_medium__bundled_fixture_with_mismatched_change_id_raises_value_error(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            with self.assertRaisesRegex(ValueError, "does not match"):
                resolve_workflow_input(
                    repo=repo_dir,
                    story_file=str(DEFAULT_TEST_STORY_FILE),
                    change_id="DIFFERENT-ID",
                )

    def test_medium__fixture_without_change_id_raises_when_caller_omits_it(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as repo_dir:
            path = Path(tmpdir) / "story.json"
            _write_fixture(
                path,
                {
                    "title": "Fixture without change id",
                    "description": "Synthetic story",
                    "acceptance_criteria": ["criterion"],
                },
            )
            with self.assertRaisesRegex(ValueError, "must include change_id"):
                resolve_workflow_input(repo=repo_dir, story_file=str(path))

    def test_medium__missing_repo_path_raises_file_not_found_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "Repository path not found"):
            resolve_workflow_input(repo="/absolute/path/to/your/repo")

    def test_medium__repo_path_that_is_a_file_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_file = Path(tmpdir) / "not-a-dir.txt"
            repo_file.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                resolve_workflow_input(repo=str(repo_file))


class ResolveWorkflowInputAcceptsExplicitChangeIdTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._repo = _make_repo_dir()
        self.addCleanup(self._repo.cleanup)
        path = Path(self._tmp.name) / "story.json"
        _write_fixture(
            path,
            {
                "title": "Fixture without change id",
                "description": "Synthetic story",
                "acceptance_criteria": ["criterion"],
            },
        )
        self.workflow_input = resolve_workflow_input(
            repo=self._repo.name,
            story_file=str(path),
            change_id="TEST-AC-777",
        )

    def test_easy__explicit_change_id_overrides_missing_fixture_change_id(self):
        self.assertEqual(self.workflow_input.change_id, "TEST-AC-777")

    def test_easy__explicit_change_id_path_intake_mode_is_synthetic(self):
        self.assertEqual(self.workflow_input.intake_mode, "synthetic")


class ResolveWorkflowInputAdoModeTests(unittest.TestCase):
    def setUp(self):
        self._repo = _make_repo_dir()
        self.addCleanup(self._repo.cleanup)
        self.workflow_input = resolve_workflow_input(
            repo=self._repo.name,
            ado_url="https://dev.azure.com/example/project/_workitems/edit/555",
        )

    def test_easy__ado_mode_intake_mode_field_is_ado(self):
        self.assertEqual(self.workflow_input.intake_mode, "ado")

    def test_easy__ado_mode_change_id_resolves_from_url_to_555(self):
        self.assertEqual(self.workflow_input.change_id, "555")

    def test_easy__ado_mode_intake_source_preserves_full_url_path(self):
        self.assertIn("_workitems/edit/555", self.workflow_input.intake_source)

    def test_easy__ado_mode_branch_description_source_is_none(self):
        self.assertIsNone(self.workflow_input.branch_description_source)


if __name__ == "__main__":
    unittest.main()
