"""
Test verification of synthetic intake artifacts.
Verifies that the intake agent correctly creates canonical artifacts for synthetic stories.

Difficulty rubric for this file:
  easy   = single-field presence or value equality on a pre-loaded YAML/JSON artifact.
  medium = schema validation across multiple required fields, or cross-checks
           between fields (e.g. raw_input.original_fixture round-trip).
  hard   = (none in this file)
"""

import json
import unittest
from pathlib import Path

import yaml

TEST_AC_001_ROOT = Path(__file__).parent.parent / "workflow-fixtures" / "TEST-AC-001"


class SyntheticIntakeArtifactsTests(unittest.TestCase):
    """Verify intake artifacts created for TEST-AC-001 synthetic story."""

    @classmethod
    def setUpClass(cls):
        agent_context = TEST_AC_001_ROOT / "intake"
        cls.story_yaml_path = agent_context / "story.yaml"
        cls.config_yaml_path = agent_context / "config.yaml"
        cls.constraints_md_path = agent_context / "constraints.md"

        with open(cls.story_yaml_path, "r") as f:
            cls.story = yaml.safe_load(f)
        with open(cls.config_yaml_path, "r") as f:
            cls.config = yaml.safe_load(f)
        with open(cls.constraints_md_path, "r") as f:
            cls.constraints = f.read()

    def test_easy__story_yaml_file_exists_on_disk(self):
        self.assertTrue(self.story_yaml_path.exists())

    def test_easy__story_yaml_parses_to_non_none_object(self):
        self.assertIsNotNone(self.story)

    def test_easy__config_yaml_file_exists_on_disk(self):
        self.assertTrue(self.config_yaml_path.exists())

    def test_easy__config_yaml_parses_to_non_none_object(self):
        self.assertIsNotNone(self.config)

    def test_easy__constraints_md_file_exists_on_disk(self):
        self.assertTrue(self.constraints_md_path.exists())

    def test_easy__constraints_md_is_non_empty(self):
        self.assertGreater(len(self.constraints), 0)

    def test_easy__story_yaml_has_change_id_field(self):
        self.assertIn("change_id", self.story)

    def test_easy__story_yaml_has_title_field(self):
        self.assertIn("title", self.story)

    def test_easy__story_yaml_has_description_field(self):
        self.assertIn("description", self.story)

    def test_easy__story_yaml_has_acceptance_criteria_field(self):
        self.assertIn("acceptance_criteria", self.story)

    def test_easy__story_yaml_has_examples_field(self):
        self.assertIn("examples", self.story)

    def test_easy__story_yaml_has_constraints_field(self):
        self.assertIn("constraints", self.story)

    def test_easy__story_yaml_has_non_functional_requirements_field(self):
        self.assertIn("non_functional_requirements", self.story)

    def test_easy__story_yaml_has_raw_input_field(self):
        self.assertIn("raw_input", self.story)

    def test_easy__story_yaml_has_metacognitive_context_field(self):
        self.assertIn("metacognitive_context", self.story)

    def test_easy__acceptance_criteria_is_a_dict(self):
        ac = self.story.get("acceptance_criteria")
        self.assertIsInstance(ac, dict)

    def test_easy__acceptance_criteria_has_ac1_key(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("AC1", ac)

    def test_easy__acceptance_criteria_has_ac2_key(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("AC2", ac)

    def test_easy__acceptance_criteria_has_ac3_key(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("AC3", ac)

    def test_easy__raw_input_section_is_present(self):
        self.assertIsNotNone(self.story.get("raw_input"))

    def test_easy__raw_input_source_type_field_is_present(self):
        self.assertIn("source_type", self.story.get("raw_input", {}))

    def test_easy__raw_input_source_type_equals_synthetic_fixture(self):
        self.assertEqual(self.story.get("raw_input", {}).get("source_type"), "synthetic_fixture")

    def test_easy__raw_input_original_fixture_field_is_present(self):
        self.assertIn("original_fixture", self.story.get("raw_input", {}))

    def test_medium__raw_input_original_fixture_round_trips_to_json_with_change_id(self):
        raw_input = self.story.get("raw_input", {})
        fixture_json_str = raw_input.get("original_fixture")
        self.assertIsNotNone(fixture_json_str)
        fixture_data = json.loads(fixture_json_str)
        self.assertEqual(fixture_data.get("change_id"), "TEST-AC-001")

    def test_easy__config_project_type_equals_synthetic_fixture(self):
        self.assertEqual(self.config.get("project_type"), "synthetic-fixture")

    def test_easy__config_yaml_has_change_id_field(self):
        self.assertIn("change_id", self.config)

    def test_easy__config_yaml_has_code_repo_field(self):
        self.assertIn("code_repo", self.config)

    def test_easy__config_yaml_has_project_type_field(self):
        self.assertIn("project_type", self.config)

    def test_easy__config_yaml_has_created_at_field(self):
        self.assertIn("created_at", self.config)

    def test_easy__story_yaml_has_no_ado_provenance(self):
        self.assertIsNone(self.story.get("ado_provenance"))

    def test_easy__constraints_md_mentions_synthetic_fixture(self):
        self.assertIn("synthetic fixture", self.constraints.lower())

    def test_easy__constraints_md_mentions_fixture_path(self):
        self.assertIn("fixture path", self.constraints.lower())

    def test_easy__ac1_text_mentions_local(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("local", ac.get("AC1", "").lower())

    def test_easy__ac2_text_mentions_preserves(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("preserves", ac.get("AC2", "").lower())

    def test_easy__ac3_text_mentions_ado_specific(self):
        ac = self.story.get("acceptance_criteria", {})
        self.assertIn("ado-specific", ac.get("AC3", "").lower())

    def test_easy__raw_input_fixture_path_field_is_present(self):
        raw_input = self.story.get("raw_input", {})
        self.assertIsNotNone(raw_input.get("fixture_path"))

    def test_easy__raw_input_fixture_path_references_synthetic_story_json(self):
        raw_input = self.story.get("raw_input", {})
        self.assertIn("synthetic_story.json", raw_input.get("fixture_path", ""))


if __name__ == "__main__":
    unittest.main()
