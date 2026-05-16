"""Tests for runner_models resolution and alias LLM config plumbing."""
import os
import unittest
from unittest.mock import patch

from core.runner_models import (
    OPENAI_COMPAT_RETRY_DEFAULTS,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
    is_copilot_runner,
    resolve_agent_llm_config,
    resolve_agent_model,
    resolve_runner_llm_config,
)


class TestResolveAgentModel(unittest.TestCase):
    """Test resolve_agent_model helper function."""

    def test_explicit_model_takes_precedence(self):
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model="claude-sonnet-4-6",
        )
        self.assertEqual(result, "claude-sonnet-4-6")

    def test_explicit_model_overrides_config_default(self):
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
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model=None,
            config={},
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["claude"])

    def test_builtin_default_for_copilot(self):
        result = resolve_agent_model(
            agent_name="intake",
            runner="copilot",
            explicit_model=None,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["copilot"])

    def test_builtin_default_for_gemini(self):
        result = resolve_agent_model(
            agent_name="intake",
            runner="gemini",
            explicit_model=None,
        )
        self.assertEqual(result, RUNNER_DEFAULT_MODELS["gemini"])

    def test_agent_not_in_config_uses_builtin(self):
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

    def test_alias_agent_default_gets_provider_prefix(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                }
            },
            "agent_model_defaults": {
                "qa-evaluator": {"openai-compat-cloud": "qwen3:32b"}
            },
        }
        result = resolve_agent_model(
            agent_name="qa-evaluator",
            runner="openai-compat-cloud",
            config=config,
        )
        self.assertEqual(result, "openai-compat/qwen3:32b")

    def test_invalid_runner_raises_error(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_agent_model(
                agent_name="intake",
                runner="invalid_runner",
            )
        self.assertIn("Unknown runner", str(ctx.exception))

    def test_empty_explicit_string_returned_as_is(self):
        config = {
            "agent_model_defaults": {
                "intake": {"claude": "claude-opus-4-6"}
            }
        }
        result = resolve_agent_model(
            agent_name="intake",
            runner="claude",
            explicit_model="",
            config=config,
        )
        self.assertEqual(result, "")

    def test_all_runners_have_defaults(self):
        for runner in ["claude", "copilot", "gemini"]:
            result = resolve_agent_model(
                agent_name="test-agent",
                runner=runner,
            )
            self.assertIsNotNone(result)
            self.assertIn(result.split("/", 1)[-1], RUNNER_MODEL_CHOICES[runner])


class RunnerLlmConfigTests(unittest.TestCase):
    def test_alias_llm_config_reads_transport_settings(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                    "base_url": "https://openai-compat.example.com",
                    "api_key_env": "OPENAI_COMPAT_CLOUD_API_KEY",
                    "extra_headers": {"X-Tenant": "acme"},
                    "litellm_extra_body": {"session": "enterprise"},
                }
            }
        }
        with patch.dict(os.environ, {"OPENAI_COMPAT_CLOUD_API_KEY": "secret"}, clear=False):
            llm_config = resolve_runner_llm_config("openai-compat-cloud", config=config)

        self.assertEqual(
            llm_config,
            {
                **OPENAI_COMPAT_RETRY_DEFAULTS,
                "model": "openai-compat/llama3.3:70b",
                "base_url": "https://openai-compat.example.com",
                "api_key": "secret",
                "extra_headers": {"X-Tenant": "acme"},
                "litellm_extra_body": {"session": "enterprise"},
            },
        )

    def test_alias_explicit_model_overrides_alias_default(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                }
            }
        }
        llm_config = resolve_runner_llm_config(
            "openai-compat-cloud",
            explicit_model="qwen3:32b",
            config=config,
        )
        self.assertEqual(llm_config, {**OPENAI_COMPAT_RETRY_DEFAULTS, "model": "openai-compat/qwen3:32b"})

    def test_alias_explicit_retry_settings_override_defaults(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                    "num_retries": 10,
                    "retry_multiplier": 3.0,
                    "retry_min_wait": 12,
                    "retry_max_wait": 180,
                    "timeout": 600,
                }
            }
        }
        llm_config = resolve_runner_llm_config("openai-compat-cloud", config=config)
        self.assertEqual(
            llm_config,
            {
                "model": "openai-compat/llama3.3:70b",
                "num_retries": 10,
                "retry_multiplier": 3.0,
                "retry_min_wait": 12,
                "retry_max_wait": 180,
                "timeout": 600,
            },
        )

    def test_alias_missing_api_key_env_raises(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                    "api_key_env": "MISSING_OPENAI_COMPAT_KEY",
                }
            }
        }
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                resolve_runner_llm_config("openai-compat-cloud", config=config)
        self.assertIn("MISSING_OPENAI_COMPAT_KEY", str(ctx.exception))

    def test_agent_llm_config_preserves_alias_transport(self):
        config = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                    "base_url": "https://openai-compat.example.com",
                }
            },
            "agent_model_defaults": {
                "qa-evaluator": {"openai-compat-cloud": "qwen3:32b"}
            },
        }
        llm_config = resolve_agent_llm_config(
            "qa-evaluator",
            "openai-compat-cloud",
            config=config,
        )
        self.assertEqual(
            llm_config,
            {
                **OPENAI_COMPAT_RETRY_DEFAULTS,
                "base_url": "https://openai-compat.example.com",
                "model": "openai-compat/qwen3:32b",
            },
        )


class CopilotRunnerDetectionTests(unittest.TestCase):
    def test_base_copilot_runner_is_copilot(self):
        self.assertTrue(is_copilot_runner("copilot"))

    def test_copilot_alias_runner_is_copilot(self):
        self.assertTrue(is_copilot_runner("copilot-deepseek"))

    def test_non_copilot_runners_are_not_copilot(self):
        self.assertFalse(is_copilot_runner("claude"))
        self.assertFalse(is_copilot_runner("gemini"))
        self.assertFalse(is_copilot_runner(None))
        self.assertFalse(is_copilot_runner(""))


if __name__ == "__main__":
    unittest.main()
