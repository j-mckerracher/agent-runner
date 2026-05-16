"""
Tests for agent prompt lookup in core.agent_prompts.

Difficulty rubric for this file:
  easy   = prompt lookup by direct filename match.
  medium = prompt lookup by front matter agent name when the filename uses a numeric prefix.
  hard   = (none in this file)
"""

import tempfile
import unittest
from pathlib import Path

from core.agent_prompts import load_agent_system_prompt


class LoadAgentSystemPromptTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
