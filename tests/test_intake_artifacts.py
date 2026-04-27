"""
Test verification of synthetic intake artifacts.
Verifies that the intake agent correctly creates canonical artifacts for synthetic stories.
"""

import json
import unittest
from pathlib import Path

import yaml


class SyntheticIntakeArtifactsTests(unittest.TestCase):
    """Verify intake artifacts created for TEST-AC-001 synthetic story."""

    @classmethod
    def setUpClass(cls):
        """Load artifacts for verification."""
        agent_context = Path(__file__).parent.parent / "agent-context" / "TEST-AC-001" / "intake"
        cls.story_yaml_path = agent_context / "story.yaml"
        cls.config_yaml_path = agent_context / "config.yaml"
        cls.constraints_md_path = agent_context / "constraints.md"

        # Load artifacts
        with open(cls.story_yaml_path, "r") as f:
            cls.story = yaml.safe_load(f)
        with open(cls.config_yaml_path, "r") as f:
            cls.config = yaml.safe_load(f)
        with open(cls.constraints_md_path, "r") as f:
            cls.constraints = f.read()

    def test_story_yaml_exists(self):
        """Verify story.yaml exists and is readable."""
        self.assertTrue(self.story_yaml_path.exists(), "story.yaml should exist")
        self.assertIsNotNone(self.story, "story.yaml should be valid YAML")

    def test_config_yaml_exists(self):
        """Verify config.yaml exists and is readable."""
        self.assertTrue(self.config_yaml_path.exists(), "config.yaml should exist")
        self.assertIsNotNone(self.config, "config.yaml should be valid YAML")

    def test_constraints_md_exists(self):
        """Verify constraints.md exists and is readable."""
        self.assertTrue(self.constraints_md_path.exists(), "constraints.md should exist")
        self.assertGreater(len(self.constraints), 0, "constraints.md should not be empty")

    def test_story_has_canonical_schema(self):
        """Verify story.yaml has all required canonical fields."""
        required_fields = [
            "change_id",
            "title",
            "description",
            "acceptance_criteria",
            "examples",
            "constraints",
            "non_functional_requirements",
            "raw_input",
            "metacognitive_context",
        ]
        for field in required_fields:
            self.assertIn(field, self.story, f"story.yaml missing required field: {field}")

    def test_acceptance_criteria_keyed_format(self):
        """Verify acceptance criteria are in keyed AC1/AC2/AC3 format."""
        ac = self.story.get("acceptance_criteria")
        self.assertIsInstance(ac, dict, "acceptance_criteria should be a dict (keyed format)")
        self.assertIn("AC1", ac, "acceptance_criteria missing AC1")
        self.assertIn("AC2", ac, "acceptance_criteria missing AC2")
        self.assertIn("AC3", ac, "acceptance_criteria missing AC3")

    def test_raw_input_preservation(self):
        """Verify raw fixture input is preserved in story.yaml."""
        raw_input = self.story.get("raw_input")
        self.assertIsNotNone(raw_input, "raw_input section missing")
        self.assertIn("source_type", raw_input, "raw_input missing source_type")
        self.assertEqual(raw_input.get("source_type"), "synthetic_fixture", "source_type should be synthetic_fixture")
        self.assertIn("original_fixture", raw_input, "raw_input missing original_fixture")

        # Verify original_fixture is valid JSON
        fixture_json_str = raw_input.get("original_fixture")
        self.assertIsNotNone(fixture_json_str, "original_fixture content is empty")
        fixture_data = json.loads(fixture_json_str)
        self.assertEqual(fixture_data.get("change_id"), "TEST-AC-001", "original_fixture should contain change_id")

    def test_config_project_type_synthetic(self):
        """Verify config.yaml has project_type=synthetic-fixture."""
        project_type = self.config.get("project_type")
        self.assertEqual(project_type, "synthetic-fixture", "project_type should be synthetic-fixture")

    def test_config_has_required_fields(self):
        """Verify config.yaml has required canonical fields."""
        required_fields = ["change_id", "code_repo", "project_type", "created_at"]
        for field in required_fields:
            self.assertIn(field, self.config, f"config.yaml missing required field: {field}")

    def test_no_ado_provenance_metadata(self):
        """Verify no ADO provenance metadata is present (synthetic mode)."""
        ado_provenance = self.story.get("ado_provenance")
        self.assertIsNone(ado_provenance, "story.yaml should not have ado_provenance (synthetic mode)")

    def test_constraints_documents_synthetic_nature(self):
        """Verify constraints.md documents that this is synthetic."""
        self.assertIn("synthetic fixture", self.constraints.lower(), "constraints.md should mention synthetic")
        self.assertIn("fixture path", self.constraints.lower(), "constraints.md should document fixture path")

    def test_acceptance_criteria_content_matches_fixture(self):
        """Verify AC content matches original fixture."""
        ac = self.story.get("acceptance_criteria", {})

        # AC1 should be about starting from local fixture
        ac1 = ac.get("AC1", "")
        self.assertIn("local", ac1.lower(), "AC1 should mention local/fixture")

        # AC2 should be about preserving raw input
        ac2 = ac.get("AC2", "")
        self.assertIn("preserves", ac2.lower(), "AC2 should mention preserving")

        # AC3 should be about skipping ADO operations
        ac3 = ac.get("AC3", "")
        self.assertIn("ado-specific", ac3.lower(), "AC3 should mention ADO-specific actions")

    def test_fixture_path_documented(self):
        """Verify the source fixture path is documented."""
        raw_input = self.story.get("raw_input", {})
        fixture_path = raw_input.get("fixture_path")
        self.assertIsNotNone(fixture_path, "fixture_path should be documented in raw_input")
        self.assertIn("synthetic_story.json", fixture_path, "fixture_path should reference synthetic_story.json")


if __name__ == "__main__":
    unittest.main()
