import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
