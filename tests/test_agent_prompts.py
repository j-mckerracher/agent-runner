"""
Tests for agent prompt lookup in core.agent_prompts.

Difficulty rubric for this file:
  easy   = prompt lookup by direct filename match.
  medium = prompt lookup by front matter agent name when the filename uses a numeric prefix.
  hard   = runtime prompt override without mutating materialized runner assets.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.agent_prompts import load_agent_system_prompt, prompt_override_env_name


class LoadAgentSystemPromptTests(unittest.TestCase):
    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDE") or key.startswith("AGENT_RUNNER_PROMPT_OVERRIDE"):
                os.environ.pop(key, None)

    def test_medium__falls_back_to_front_matter_name_when_filename_does_not_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            prompt_path = prompts_dir / "05-qa.agent.md"
            prompt_path.write_text(
                "---\nname: qa-engineer\n---\n\n<agent>\nQA prompt body\n</agent>\n",
                encoding="utf-8",
            )

            prompt = load_agent_system_prompt("qa-engineer", prompts_dir=prompts_dir, runner="copilot")

        self.assertEqual(prompt, "QA prompt body")

    def test_hard__uses_runtime_prompt_override_before_materialized_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir)
            materialized = prompts_dir / "02-task-generator.agent.md"
            materialized.write_text(
                "---\nname: task-generator\n---\n\n<agent>\nMaterialized body\n</agent>\n",
                encoding="utf-8",
            )
            override = prompts_dir / "candidate.md"
            override.write_text(
                "---\nname: task-generator\n---\n\n<agent>\nCandidate body\n</agent>\n",
                encoding="utf-8",
            )
            os.environ[prompt_override_env_name("task-generator")] = str(override)

            prompt = load_agent_system_prompt("task-generator", prompts_dir=prompts_dir, runner="openai-compat")

        self.assertEqual(prompt, "Candidate body")

    def test_hard__invalid_prompt_override_path_raises_clear_error(self):
        os.environ[prompt_override_env_name("task-generator")] = "/no/such/prompt.md"
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                load_agent_system_prompt("task-generator", prompts_dir=Path(tmpdir), runner="openai-compat")


if __name__ == "__main__":
    unittest.main()
