import unittest
from pathlib import Path

from eval.check_helpers import contains_check
from eval.models import AcceptanceCriterion, EvalStory
from eval.plugin_loader import PluginLoadError, load_plugin, plugin_checks
from eval.story_checks import run_story_checks


FIXTURES = Path(__file__).parent / "fixtures" / "eval_plugins"


def story():
    return EvalStory(
        story_id="story-001",
        title="Story 001",
        description="A test story",
        acceptance_criteria=[
            AcceptanceCriterion(
                ac_id="AC1",
                text="Built-in check",
                tier="easy",
                check=contains_check("builtin_contains", "Built-in contains", "agent_output", "implemented"),
            )
        ],
    )


class PluginLoaderTests(unittest.TestCase):
    def test_valid_plugin_loads_and_returns_checks(self):
        plugin = load_plugin(FIXTURES / "valid_plugin.py")

        checks = plugin_checks(plugin, story())

        self.assertEqual(checks[0].id, "plugin_contains_title")

    def test_loaded_plugin_is_not_validated_twice(self):
        plugin = load_plugin(FIXTURES / "counting_plugin.py")

        plugin_checks(plugin, story())

        self.assertEqual(plugin.validate_calls, 1)

    def test_run_story_checks_accepts_plugin_path(self):
        results = run_story_checks(
            "story-001",
            "implemented Story 001",
            suite_story=story(),
            plugin=FIXTURES / "valid_plugin.py",
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.passed for result in results))

    def test_api_mismatch_fails_loudly(self):
        with self.assertRaisesRegex(PluginLoadError, "API mismatch"):
            load_plugin(FIXTURES / "api_mismatch_plugin.py")

    def test_validate_failure_fails_loudly(self):
        with self.assertRaisesRegex(PluginLoadError, "validate"):
            load_plugin(FIXTURES / "validation_fails_plugin.py")

    def test_missing_validate_fails_loudly(self):
        with self.assertRaisesRegex(PluginLoadError, "validate"):
            load_plugin(FIXTURES / "missing_validate_plugin.py")

    def test_plugin_check_id_collision_fails_loudly(self):
        class CollisionPlugin:
            api_version = "1.0"

            def validate(self):
                return None

            def get_checks(self, eval_story):
                return [contains_check("builtin_contains", "Collision", "agent_output", eval_story.title)]

        with self.assertRaisesRegex(PluginLoadError, "Duplicate check id"):
            plugin_checks(CollisionPlugin(), story(), built_in_checks=[story().acceptance_criteria[0].check])

    def test_plugin_story_id_mismatch_fails_loudly(self):
        class MismatchPlugin:
            api_version = "1.0"
            story_id = "story-999"

            def validate(self):
                return None

            def get_checks(self, eval_story):
                return [contains_check("plugin", "Plugin", "agent_output", eval_story.title)]

        with self.assertRaisesRegex(PluginLoadError, "story_id mismatch"):
            plugin_checks(MismatchPlugin(), story())

    def test_disallowed_import_fails_before_import(self):
        with self.assertRaisesRegex(PluginLoadError, "outside the allowed"):
            load_plugin(FIXTURES / "disallowed_import_plugin.py")


if __name__ == "__main__":
    unittest.main()
