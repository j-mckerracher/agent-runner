import subprocess
import unittest
from unittest.mock import patch

import run_cmds


class RunCmdsTests(unittest.TestCase):
    def test_run_gemini_cmd_injects_agent_prompt_and_uses_headless_flags(self):
        completed = subprocess.CompletedProcess(
            args=["gemini"],
            returncode=0,
            stdout="ok",
            stderr="",
        )

        with (
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules") as load_prompt,
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            result = run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")

        self.assertEqual(result, "ok")
        load_prompt.assert_called_once_with("intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertEqual(called_cmd[:2], ["gemini", "-p"])
        self.assertIn("Agent rules", called_cmd[2])
        self.assertIn("Do the work", called_cmd[2])
        self.assertIn("intake-agent", called_cmd[2])
        self.assertIn("--model", called_cmd)
        self.assertIn("gemini-2.5-flash", called_cmd)
        self.assertIn("--output-format", called_cmd)
        self.assertIn("text", called_cmd)
        self.assertIn("--yolo", called_cmd)

    def test_run_agent_cmd_maps_runner_model_to_gemini_model(self):
        with patch.object(run_cmds, "run_gemini_cmd", return_value="gemini output") as run_gemini:
            result = run_cmds.run_agent_cmd(
                runner="gemini",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
                runner_model="gemini-3-pro-preview",
            )

        self.assertEqual(result, "gemini output")
        run_gemini.assert_called_once_with(
            prompt="Implement the task",
            agent="software-engineer-hyperagent",
            model="gemini-3-pro-preview",
        )

    def test_run_agent_cmd_dispatches_gemini_backend(self):
        with patch.object(run_cmds, "run_gemini_cmd", return_value="gemini output") as run_gemini:
            result = run_cmds.run_agent_cmd(
                runner="gemini",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
            )

        self.assertEqual(result, "gemini output")
        run_gemini.assert_called_once_with(
            prompt="Implement the task",
            agent="software-engineer-hyperagent",
        )

    def test_run_agent_cmd_rejects_unknown_runner(self):
        with self.assertRaisesRegex(ValueError, "claude', 'copilot', or 'gemini"):
            run_cmds.run_agent_cmd(runner="unknown", prompt="prompt", agent="agent")


if __name__ == "__main__":
    unittest.main()
