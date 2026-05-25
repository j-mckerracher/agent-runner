import unittest
from unittest.mock import MagicMock, patch

from core.opik_integration import call_evaluator_sdk


class OpenaiCompatEvaluatorSdkTests(unittest.TestCase):
    def test_medium__call_evaluator_sdk_uses_openai_compat_for_openai_compat_alias(self):
        with (
            patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM PROMPT"),
            patch("core.opik_integration.inject_file_contents", return_value=""),
            patch("core.opik_integration._load_runtime_config_for_provider", return_value={}),
            patch("core.opik_integration._provider_for_runner", return_value="openai-compat"),
            patch("core.opik_integration.run_openai_compat_text", return_value="PASS") as run_openai_compat_text,
        ):
            result = call_evaluator_sdk(
                context="Evaluate the generated report.",
                agent_name="qa-evaluator",
                model="openai-compat/deepseek-v4-pro:cloud",
                runner="ds4",
            )

        self.assertEqual(result, "PASS")
        run_openai_compat_text.assert_called_once_with(
            prompt="Evaluate the generated report.",
            system_prompt="SYSTEM PROMPT",
            model="openai-compat/deepseek-v4-pro:cloud",
            runner="ds4",
        )


class EvaluatorSdkModelMatrixTests(unittest.TestCase):
    """For every (runner, model) in RUNNER_MODEL_CHOICES, verify call_evaluator_sdk
    passes the exact selected model to the correct provider SDK/function."""

    @classmethod
    def setUpClass(cls):
        from core.runner_models import RUNNER_MODEL_CHOICES as _choices
        cls._choices = _choices

    # -- copilot ---------------------------------------------------------------

    def test_medium__copilot_evaluator_passes_model(self):
        for model in self._choices["copilot"]:
            with self.subTest(model=model):
                with (
                    patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
                    patch("core.opik_integration.inject_file_contents", return_value=""),
                    patch("core.opik_integration.run_copilot_cmd", return_value="OK") as run_fn,
                ):
                    result = call_evaluator_sdk(
                        context="Evaluate the report.",
                        agent_name="qa-evaluator",
                        model=model,
                        runner="copilot",
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)

    # -- codex ----------------------------------------------------------------

    def test_medium__codex_evaluator_passes_model(self):
        for model in self._choices["codex"]:
            with self.subTest(model=model):
                with (
                    patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
                    patch("core.opik_integration.inject_file_contents", return_value=""),
                    patch("core.opik_integration.run_codex_cmd", return_value="OK") as run_fn,
                ):
                    result = call_evaluator_sdk(
                        context="Evaluate the report.",
                        agent_name="qa-evaluator",
                        model=model,
                        runner="codex",
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)

    # -- openai-compat ---------------------------------------------------------

    def test_medium__openai_compat_evaluator_passes_model(self):
        for model in self._choices["openai-compat"]:
            with self.subTest(model=model):
                with (
                    patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
                    patch("core.opik_integration.inject_file_contents", return_value=""),
                    patch("core.opik_integration.run_openai_compat_text", return_value="OK") as run_fn,
                ):
                    result = call_evaluator_sdk(
                        context="Evaluate the report.",
                        agent_name="qa-evaluator",
                        model=model,
                        runner="openai-compat",
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)
                self.assertEqual(run_fn.call_args.kwargs.get("runner"), "openai-compat")

    # -- claude ----------------------------------------------------------------

    def test_medium__claude_evaluator_passes_model(self):
        for model in self._choices["claude"]:
            with self.subTest(model=model):
                mock_response = MagicMock()
                mock_response.content = [MagicMock(text="OK")]
                mock_client = MagicMock()
                mock_client.messages.create.return_value = mock_response

                with (
                    patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
                    patch("core.opik_integration.inject_file_contents", return_value=""),
                    patch("core.opik_integration.anthropic.Anthropic", return_value=mock_client),
                ):
                    result = call_evaluator_sdk(
                        context="Evaluate the report.",
                        agent_name="qa-evaluator",
                        model=model,
                        runner="claude",
                    )
                self.assertEqual(result, "OK")
                mock_client.messages.create.assert_called_once()
                self.assertEqual(
                    mock_client.messages.create.call_args.kwargs.get("model"), model
                )

    # -- gemini ----------------------------------------------------------------

    def test_medium__gemini_evaluator_passes_model(self):
        for model in self._choices["gemini"]:
            with self.subTest(model=model):
                mock_response = MagicMock()
                mock_response.text = "OK"
                mock_client = MagicMock()
                mock_client.models.generate_content.return_value = mock_response

                with (
                    patch("core.opik_integration.build_runner_agent_instructions", return_value="SYSTEM"),
                    patch("core.opik_integration.inject_file_contents", return_value=""),
                    patch("core.opik_integration.google_genai.Client", return_value=mock_client),
                ):
                    result = call_evaluator_sdk(
                        context="Evaluate the report.",
                        agent_name="qa-evaluator",
                        model=model,
                        runner="gemini",
                    )
                self.assertEqual(result, "OK")
                mock_client.models.generate_content.assert_called_once()
                self.assertEqual(
                    mock_client.models.generate_content.call_args.kwargs.get("model"),
                    model,
                )


if __name__ == "__main__":
    unittest.main()
