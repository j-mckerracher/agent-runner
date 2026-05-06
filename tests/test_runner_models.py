"""Tests for runner_models module, especially model resolution and dispatching."""
import unittest

from core.runner_models import (
    resolve_agent_model,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
)


class TestResolveAgentModel(unittest.TestCase):
    """Test resolve_agent_model helper function."""

    def test_explicit_model_takes_precedence(self):
        """Explicit model should be returned as-is."""
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model="claude-sonnet-4-6",
        )
        self.assertEqual(result, "claude-sonnet-4-6")

    def test_explicit_model_overrides_config_default(self):
        """Explicit model should override config defaults."""
        config = {
            "agent_model_defaults": {
                "intake": {"claude": "claude-haiku-4-5-20251001"}
            }
        }
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model="claude-sonnet-4-6",
            config=config,
        )
        self.assertEqual(result, "claude-sonnet-4-6")

    def test_config_default_when_no_explicit_model(self):
        """Config default should be used when explicit model is None."""
        config = {
            "agent_model_defaults": {
                "task-plan-evaluator": {"copilot": "gpt-5.5"}
            }
        }
        result = resolve_agent_model(
            agent_name="task-plan-evaluator",
            runner="copilot",
            explicit_model=None,
            config=config,
        )
        self.assertEqual(result, "gpt-5.5")

    def test_builtin_default_when_no_config_or_explicit(self):
        """Built-in runner default should be used as fallback."""
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model=None,
            config={},
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["claude"])

    def test_builtin_default_for_copilot(self):
        """Built-in Copilot default should be gpt-5-mini."""
        result = resolve_agent_model(
            agent_name="intake",
            runner="copilot",
            explicit_model=None,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["copilot"])

    def test_builtin_default_for_gemini(self):
        """Built-in Gemini default should be gemini-2.5-flash."""
        result = resolve_agent_model(
            agent_name="intake",
            runner="gemini",
            explicit_model=None,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["gemini"])

    def test_agent_not_in_config_uses_builtin(self):
        """If agent not in config, should use built-in default."""
        config = {
            "agent_model_defaults": {
                "other-agent": {"claude": "claude-opus-4-6"}
            }
        }
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model=None,
            config=config,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["claude"])

    def test_runner_not_in_config_for_agent_uses_builtin(self):
        """If runner not in agent config, should use built-in default."""
        config = {
            "agent_model_defaults": {
                "intake": {"gemini": "gemini-2.5-pro"}
            }
        }
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model=None,
            config=config,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["claude"])

    def test_invalid_runner_raises_error(self):
        """Unknown runner should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            resolve_agent_model(
                agent_name="intake",
                runner="invalid_runner",
            )
        self.assertIn("Unknown runner", str(ctx.exception))

    def test_empty_explicit_string_returned_as_is(self):
        """Empty string explicit model is still considered explicit and returned."""
        config = {
            "agent_model_defaults": {
                "intake": {"claude": "claude-opus-4-6"}
            }
        }
        # Empty string is explicitly provided, so it should be returned (even though invalid)
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model="",
            config=config,
        )
        self.assertEqual(result, "")

    def test_all_runners_have_defaults(self):
        """All valid runners should have built-in defaults."""
        for runner in ["claude", "copilot", "gemini"]:
            result = resolve_agent_model(
                agent_name="test-agent",
                runner=runner,
            )
            self.assertIsNotNone(result)
            self.assertIn(result, RUNNER_MODEL_CHOICES[runner])


if __name__ == "__main__":
    unittest.main()
