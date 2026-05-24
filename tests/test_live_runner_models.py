"""Opt-in live smoke tests for every runner/model in RUNNER_MODEL_CHOICES.

These tests make REAL calls to external CLIs and APIs. They are skipped
by default in CI. Enable with:

    AGENT_RUNNER_LIVE_MODEL_SMOKE=1 python -m unittest tests.test_live_runner_models

Prerequisites per runner:
  - claude:      `claude` CLI on PATH, ANTHROPIC_API_KEY set
  - copilot:     `copilot` CLI on PATH
  - gemini:      `gemini` CLI on PATH, GEMINI_API_KEY set
  - openai-compat: local OpenAI-compatible server at OPENAI_COMPAT_HOST
                   (default http://127.0.0.1:11434)
"""
import os
import unittest

SMOKE_PROMPT = "Reply exactly RUNNER_MODEL_SMOKE_OK"


@unittest.skipUnless(
    os.environ.get("AGENT_RUNNER_LIVE_MODEL_SMOKE") == "1",
    "Set AGENT_RUNNER_LIVE_MODEL_SMOKE=1 to enable live model smoke tests",
)
class LiveRunnerModelSmokeTests(unittest.TestCase):
    """Live smoke test: every (runner, model) pair gets a tiny prompt and
    must return output containing RUNNER_MODEL_SMOKE_OK."""

    @classmethod
    def setUpClass(cls):
        from core.runner_models import RUNNER_MODEL_CHOICES as _choices
        from core.run_cmds import run_agent_cmd
        cls._choices = _choices
        cls._run = run_agent_cmd

    def _check_prerequisites(self, runner):
        import shutil
        known_prereqs = {
            "claude": "claude",
            "copilot": "copilot",
            "gemini": "gemini",
            "openai-compat": None,  # Uses HTTP API, no CLI required
        }
        cli_name = known_prereqs.get(runner)
        if cli_name and not shutil.which(cli_name):
            self.skipTest(f"{cli_name!r} CLI not found on PATH — cannot live-smoke {runner}")

    def test_medium__all_models_return_smoke_ok(self):
        for runner, models in self._choices.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    self._check_prerequisites(runner)

                    result = self._run(
                        runner=runner,
                        prompt=SMOKE_PROMPT,
                        agent="qa-evaluator",
                        runner_model=model,
                    )

                    self.assertIsNotNone(result)
                    self.assertNotEqual(result.strip(), "")
                    self.assertIn(
                        "RUNNER_MODEL_SMOKE_OK",
                        result,
                        f"runner={runner} model={model}: output did not contain "
                        f"RUNNER_MODEL_SMOKE_OK. Got: {result[:200]}",
                    )


if __name__ == "__main__":
    unittest.main()
