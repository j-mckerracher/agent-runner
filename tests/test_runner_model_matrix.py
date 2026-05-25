"""Matrix tests over RUNNER_MODEL_CHOICES to ensure every built-in model
is correctly registered and resolves through the model-resolution functions.

Difficulty rubric:
  easy   = single-function registry or resolution assertions.
  medium = matrix iteration over all runner/model pairs with subTest.
"""
import unittest

from core.runner_models import (
    KNOWN_RUNNERS,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
    resolve_agent_model,
    resolve_runner_llm_config,
    resolve_runner_model,
)


class RegistryConsistencyTests(unittest.TestCase):
    """Structural consistency checks for the runner/model registries."""

    def test_easy__registry_keys_match(self):
        self.assertEqual(
            set(RUNNER_MODEL_CHOICES),
            set(RUNNER_DEFAULT_MODELS),
            set(KNOWN_RUNNERS),
        )

    def test_easy__every_default_in_choices(self):
        for runner, default_model in RUNNER_DEFAULT_MODELS.items():
            with self.subTest(runner=runner):
                choices = RUNNER_MODEL_CHOICES[runner]
                self.assertIn(default_model, choices)

    def test_easy__every_runner_has_at_least_one_model(self):
        for runner in KNOWN_RUNNERS:
            with self.subTest(runner=runner):
                choices = RUNNER_MODEL_CHOICES[runner]
                self.assertGreater(len(choices), 0)

    def test_easy__no_duplicate_models_within_runner(self):
        for runner in KNOWN_RUNNERS:
            with self.subTest(runner=runner):
                choices = RUNNER_MODEL_CHOICES[runner]
                self.assertEqual(len(choices), len(set(choices)))

    def test_easy__openai_compat_included_in_registries(self):
        self.assertIn("openai-compat", KNOWN_RUNNERS)
        self.assertIn("openai-compat", RUNNER_MODEL_CHOICES)
        self.assertIn("openai-compat", RUNNER_DEFAULT_MODELS)

    def test_easy__codex_included_in_registries(self):
        self.assertIn("codex", KNOWN_RUNNERS)
        self.assertIn("codex", RUNNER_MODEL_CHOICES)
        self.assertIn("codex", RUNNER_DEFAULT_MODELS)


class ResolveRunnerLlmConfigMatrixTests(unittest.TestCase):
    """For every (runner, model) pair: resolve_runner_llm_config resolves
    and returns the exact model string (no provider prefix for built-in runners
    since RUNNER_MODEL_PROVIDERS is empty)."""

    def test_medium__all_pairs_resolve_runner_llm_config(self):
        for runner, models in RUNNER_MODEL_CHOICES.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    llm_config = resolve_runner_llm_config(runner, explicit_model=model)
                    resolved = llm_config["model"]
                    self.assertEqual(
                        resolved, model,
                        f"resolve_runner_llm_config({runner!r}, model={model!r}) "
                        f"returned model={resolved!r}, expected {model!r}"
                    )

    def test_medium__all_pairs_resolve_agent_model(self):
        for runner, models in RUNNER_MODEL_CHOICES.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    resolved = resolve_agent_model(
                        "qa-evaluator", runner, explicit_model=model
                    )
                    self.assertEqual(
                        resolved, model,
                        f"resolve_agent_model('qa-evaluator', {runner!r}, model={model!r}) "
                        f"returned {resolved!r}, expected {model!r}"
                    )

    def test_medium__resolve_runner_model_preserves_exact_model_for_builtins(self):
        for runner, models in RUNNER_MODEL_CHOICES.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    resolved = resolve_runner_model(runner, explicit_model=model)
                    self.assertEqual(
                        resolved, model,
                        f"resolve_runner_model({runner!r}, model={model!r}) "
                        f"returned {resolved!r}, expected {model!r}"
                    )

    def test_easy__invalid_model_rejected_per_runner(self):
        for runner in KNOWN_RUNNERS:
            if runner in {"codex", "openai-compat"}:
                continue
            with self.subTest(runner=runner):
                with self.assertRaises(ValueError):
                    resolve_runner_llm_config(runner, explicit_model="definitely-not-a-valid-model")


class RunnerDefaultModelTests(unittest.TestCase):
    """Verify every built-in runner resolves to its default model when no
    explicit model is provided."""

    def test_easy__all_runners_resolve_default_model(self):
        for runner in KNOWN_RUNNERS:
            with self.subTest(runner=runner):
                llm_config = resolve_runner_llm_config(runner, explicit_model=None)
                resolved = llm_config["model"]
                expected = RUNNER_DEFAULT_MODELS[runner]
                self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
