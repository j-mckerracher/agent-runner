from __future__ import annotations

import json
import unittest
from pathlib import Path


RUNNER_ROOT = Path(__file__).resolve().parent.parent


def _read_json(relative_path: str) -> dict:
    return json.loads((RUNNER_ROOT / relative_path).read_text(encoding="utf-8"))


class RtkHookConfigTests(unittest.TestCase):
    def test_easy__claude_registers_rtk_for_bash_pre_tool_use(self) -> None:
        settings = _read_json(".claude/settings.json")
        groups = settings["hooks"]["PreToolUse"]

        self.assertTrue(
            any(
                group.get("matcher") == "Bash"
                and any(hook.get("command") == "rtk hook claude" for hook in group.get("hooks", []))
                for group in groups
            )
        )

    def test_easy__gemini_registers_rtk_for_shell_before_tool_and_keeps_sanitizer(self) -> None:
        settings = _read_json(".gemini/settings.json")
        before_tool = settings["hooks"]["BeforeTool"]
        after_tool = settings["hooks"]["AfterTool"]

        self.assertTrue(
            any(
                group.get("matcher") == "run_shell_command"
                and any(hook.get("command") == "rtk hook gemini" for hook in group.get("hooks", []))
                for group in before_tool
            )
        )
        self.assertTrue(
            any(
                group.get("matcher") == "write_file|replace_in_file|replace"
                and any(
                    hook.get("command") == ".github/scripts/sanitize-artifact-hook.sh"
                    for hook in group.get("hooks", [])
                )
                for group in after_tool
            )
        )

    def test_easy__codex_registers_rtk_for_bash_pre_tool_use(self) -> None:
        settings = _read_json(".codex/hooks/rtk-rewrite.json")
        groups = settings["hooks"]["PreToolUse"]

        self.assertTrue(
            any(
                group.get("matcher") == "Bash"
                and any(hook.get("command") == "rtk hook copilot" for hook in group.get("hooks", []))
                for group in groups
            )
        )

    def test_easy__copilot_registers_rtk_for_bash_pre_tool_use(self) -> None:
        settings = _read_json(".github/hooks/rtk-rewrite.json")
        groups = settings["hooks"]["preToolUse"]

        self.assertEqual(settings["version"], 1)
        self.assertTrue(
            any(
                group.get("matcher") == "bash"
                and group.get("command") == "rtk hook copilot"
                and group.get("timeoutSec") == 5
                for group in groups
            )
        )
