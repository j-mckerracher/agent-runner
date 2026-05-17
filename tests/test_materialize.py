import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import core.materialize as materialize


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="materialize-test-"))
        self.agent_sources = self.tmpdir / "agent-definition-source"
        self.skill_sources = self.tmpdir / "agent-skill-source"
        self.script_sources = self.tmpdir / "agent-script-source"

        intake_dir = self.agent_sources / "intake" / "v1"
        intake_dir.mkdir(parents=True)
        (intake_dir / "manifest.yaml").write_text(
            """
name: intake
version: v1
claude_code_agent_file: intake.agent.md
""".strip() + "\n",
            encoding="utf-8",
        )
        (intake_dir / "prompt.md").write_text("# Intake Agent Prompt\n", encoding="utf-8")

        skill_dir = self.skill_sources / "frontend-design" / "v1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            """
name: frontend-design
version: v1
skill_file: SKILL.md
""".strip() + "\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.md").write_text("# Frontend Design\n", encoding="utf-8")

        self.script_sources.mkdir(parents=True)
        (self.script_sources / "validate-scope.py").write_text("print('validate scope')\n", encoding="utf-8")

        self.runner_agent_dirs = {
            "claude": self.tmpdir / ".claude" / "agents",
            "copilot": self.tmpdir / ".github" / "agents",
            "gemini": self.tmpdir / ".gemini" / "agents",
        }
        self.runner_skill_dirs = {
            "claude": self.tmpdir / ".claude" / "skills",
            "copilot": self.tmpdir / ".github" / "skills",
            "gemini": self.tmpdir / ".gemini" / "skills",
        }
        self.runner_script_dirs = {
            "claude": self.tmpdir / ".claude" / "scripts",
            "copilot": self.tmpdir / ".github" / "scripts",
            "gemini": self.tmpdir / ".gemini" / "scripts",
        }
        self.runner_metadata_files = {
            "claude": self.tmpdir / ".claude" / ".materialization.json",
            "copilot": self.tmpdir / ".github" / ".materialization.json",
            "gemini": self.tmpdir / ".gemini" / ".materialization.json",
        }

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_paths(self):
        return (
            patch.object(materialize, "RUNNER_ROOT", self.tmpdir),
            patch.object(materialize, "AGENT_SOURCES_ROOT", self.agent_sources),
            patch.object(materialize, "SKILL_SOURCES_ROOT", self.skill_sources),
            patch.object(materialize, "SCRIPT_SOURCES_ROOT", self.script_sources),
            patch.dict(materialize.RUNNER_AGENT_DIRS, self.runner_agent_dirs, clear=True),
            patch.dict(materialize.RUNNER_SKILL_DIRS, self.runner_skill_dirs, clear=True),
            patch.dict(materialize.RUNNER_SCRIPT_DIRS, self.runner_script_dirs, clear=True),
            patch.dict(materialize.RUNNER_METADATA_FILES, self.runner_metadata_files, clear=True),
        )

    def test_medium__run_materialization_writes_agents_and_skills_for_all_runners(self):
        with ExitStack() as stack:
            for patcher in self._patch_paths():
                stack.enter_context(patcher)
            result = materialize.run_materialization()

        self.assertTrue(result)
        for runner in ("claude", "copilot", "gemini"):
            self.assertTrue((self.runner_agent_dirs[runner] / "intake.agent.md").exists())
            self.assertTrue((self.runner_skill_dirs[runner] / "frontend-design" / "SKILL.md").exists())
            self.assertTrue((self.runner_script_dirs[runner] / "validate-scope.py").exists())
            self.assertTrue(self.runner_metadata_files[runner].exists())

    def test_medium__run_materialization_check_only_detects_skill_drift(self):
        with ExitStack() as stack:
            for patcher in self._patch_paths():
                stack.enter_context(patcher)
            self.assertTrue(materialize.run_materialization())
            (self.skill_sources / "frontend-design" / "v1" / "SKILL.md").write_text(
                "# Frontend Design\nUpdated\n",
                encoding="utf-8",
            )
            self.assertFalse(materialize.run_materialization(check_only=True))

    def test_medium__run_materialization_check_only_detects_script_drift(self):
        with ExitStack() as stack:
            for patcher in self._patch_paths():
                stack.enter_context(patcher)
            self.assertTrue(materialize.run_materialization())
            (self.script_sources / "validate-scope.py").write_text(
                "print('updated validate scope')\n",
                encoding="utf-8",
            )
            self.assertFalse(materialize.run_materialization(check_only=True))

    def test_medium__run_materialization_uses_latest_agent_version_and_skill_manifest_name(self):
        intake_v2_dir = self.agent_sources / "intake" / "v2"
        intake_v2_dir.mkdir(parents=True)
        (intake_v2_dir / "manifest.yaml").write_text(
            """
name: intake
version: v2
claude_code_agent_file: intake.agent.md
""".strip() + "\n",
            encoding="utf-8",
        )
        (intake_v2_dir / "prompt.md").write_text(
            """
# Intake Agent Prompt v2

## Required Skills

| Skill | Purpose |
| --- | --- |
| **interrogate-eng** | Clarify blocking ambiguity |
""".strip() + "\n",
            encoding="utf-8",
        )

        interrogation_dir = self.skill_sources / "interrogation" / "v1"
        interrogation_dir.mkdir(parents=True)
        (interrogation_dir / "manifest.yaml").write_text(
            """
name: interrogate-eng
version: v1
skill_file: SKILL.md
""".strip() + "\n",
            encoding="utf-8",
        )
        (interrogation_dir / "SKILL.md").write_text("# interrogate-eng\n", encoding="utf-8")

        with ExitStack() as stack:
            for patcher in self._patch_paths():
                stack.enter_context(patcher)
            self.assertTrue(materialize.run_materialization())

        for runner in ("claude", "copilot", "gemini"):
            agent_text = (self.runner_agent_dirs[runner] / "intake.agent.md").read_text(encoding="utf-8")
            self.assertIn("# Intake Agent Prompt v2", agent_text)
            self.assertTrue((self.runner_skill_dirs[runner] / "interrogate-eng" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()

