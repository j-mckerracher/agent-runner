# Difficulty rubric for this file:
#   easy   = single value/return assertion on a mocked subprocess call.
#   medium = inspection of dispatched argv/env composition or kwargs forwarding.
#   hard   = (none in this file)

import os
import subprocess
import unittest
from unittest.mock import patch

import run_cmds


class RunCopilotCmdEnvironmentTests(unittest.TestCase):
    def _patched_run(self):
        return subprocess.CompletedProcess(args=["copilot"], returncode=0, stdout="ok", stderr="")

    def test_easy__copilot_run_returns_subprocess_stdout(self):
        completed = self._patched_run()
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-key", "CLAUDE_CODE_API_KEY": "claude-code-key"}, clear=False),
            patch.object(run_cmds.subprocess, "run", return_value=completed),
        ):
            result = run_cmds.run_copilot_cmd(prompt="Do the work", agent="intake-agent")
        self.assertEqual(result, "ok")

    def test_medium__copilot_env_excludes_anthropic_api_key(self):
        completed = self._patched_run()
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-key", "CLAUDE_CODE_API_KEY": "claude-code-key"}, clear=False),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_copilot_cmd(prompt="Do the work", agent="intake-agent")
        called_env = run_subprocess.call_args.kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", called_env)

    def test_medium__copilot_env_excludes_claude_code_api_key(self):
        completed = self._patched_run()
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-key", "CLAUDE_CODE_API_KEY": "claude-code-key"}, clear=False),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_copilot_cmd(prompt="Do the work", agent="intake-agent")
        called_env = run_subprocess.call_args.kwargs["env"]
        self.assertNotIn("CLAUDE_CODE_API_KEY", called_env)

    def test_medium__copilot_retries_transient_alias_failure_until_success(self):
        transient = subprocess.CompletedProcess(
            args=["copilot-gemma4"], returncode=1, stdout="", stderr='Error: Post "https://ollama.com:443/api/show": http2: server sent GOAWAY'
        )
        success = subprocess.CompletedProcess(args=["copilot-gemma4"], returncode=0, stdout="ok", stderr="")
        with (
            patch.object(run_cmds, "_run_cli", side_effect=[transient, success]) as run_cli,
            patch.object(run_cmds.time, "sleep") as sleep,
        ):
            result = run_cmds.run_copilot_cmd(prompt="Do the work", agent="intake-agent", cli_cmd="copilot-gemma4")
        self.assertEqual(result, "ok")
        self.assertEqual(run_cli.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_medium__copilot_does_not_retry_nontransient_failure(self):
        failed = subprocess.CompletedProcess(args=["copilot"], returncode=1, stdout="", stderr="validation error")
        with (
            patch.object(run_cmds, "_run_cli", return_value=failed) as run_cli,
            patch.object(run_cmds.time, "sleep") as sleep,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            run_cmds.run_copilot_cmd(prompt="Do the work", agent="intake-agent")
        run_cli.assert_called_once()
        sleep.assert_not_called()


class RunGeminiCmdShapeTests(unittest.TestCase):
    def _run(self):
        completed = subprocess.CompletedProcess(args=["gemini"], returncode=0, stdout="ok", stderr="")
        env = {"ANTHROPIC_API_KEY": "anthropic-key", "CLAUDE_CODE_API_KEY": "claude-code-key"}
        return completed, env

    def test_easy__gemini_run_returns_subprocess_stdout(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed),
        ):
            result = run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        self.assertEqual(result, "ok")

    def test_medium__gemini_loads_agent_system_prompt_for_intake_agent(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules") as load_prompt,
            patch.object(run_cmds.subprocess, "run", return_value=completed),
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        load_prompt.assert_called_once_with("intake-agent")

    def test_medium__gemini_argv_starts_with_gemini_dash_p(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertEqual(called_cmd[:2], ["gemini", "-p"])

    def test_medium__gemini_prompt_argument_includes_agent_rules(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("Agent rules", called_cmd[2])

    def test_medium__gemini_prompt_argument_includes_user_prompt(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("Do the work", called_cmd[2])

    def test_medium__gemini_prompt_argument_includes_agent_slug(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("intake-agent", called_cmd[2])

    def test_medium__gemini_argv_passes_model_flag(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("--model", called_cmd)

    def test_medium__gemini_argv_uses_gemini_2_5_flash_by_default(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("gemini-2.5-flash", called_cmd)

    def test_medium__gemini_argv_uses_text_output_format(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("--output-format", called_cmd)
        self.assertIn("text", called_cmd)

    def test_medium__gemini_argv_passes_yolo_flag(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_cmd = run_subprocess.call_args.args[0]
        self.assertIn("--yolo", called_cmd)

    def test_medium__gemini_env_excludes_anthropic_api_key(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_env = run_subprocess.call_args.kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", called_env)

    def test_medium__gemini_env_excludes_claude_code_api_key(self):
        completed, env = self._run()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(run_cmds, "load_agent_system_prompt", return_value="Agent rules"),
            patch.object(run_cmds.subprocess, "run", return_value=completed) as run_subprocess,
        ):
            run_cmds.run_gemini_cmd(prompt="Do the work", agent="intake-agent")
        called_env = run_subprocess.call_args.kwargs["env"]
        self.assertNotIn("CLAUDE_CODE_API_KEY", called_env)


class RunClaudeCmdTests(unittest.TestCase):
    def _completed(self):
        return subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="ok", stderr="")

    def test_easy__claude_run_returns_subprocess_stdout(self):
        with patch.object(run_cmds.subprocess, "run", return_value=self._completed()):
            result = run_cmds.run_claude_cmd(prompt="Do the work", agent="intake-agent")
        self.assertEqual(result, "ok")

    def test_medium__claude_subprocess_call_omits_explicit_env_kwarg(self):
        with patch.object(run_cmds.subprocess, "run", return_value=self._completed()) as run_subprocess:
            run_cmds.run_claude_cmd(prompt="Do the work", agent="intake-agent")
        self.assertNotIn("env", run_subprocess.call_args.kwargs)


class RunCliErrorEmissionTests(unittest.TestCase):
    def test_medium__run_cli_emits_structured_error_log_for_nonzero_exit(self):
        completed = subprocess.CompletedProcess(
            args=["copilot"], returncode=7, stdout="stdout line", stderr="stderr detail"
        )
        with (
            patch.object(run_cmds.subprocess, "run", return_value=completed),
            patch.object(run_cmds, "_emit_event") as emit_event,
        ):
            result = run_cmds._run_cli(["copilot", "-p", "prompt"], runner="copilot", agent="intake-agent")
        self.assertEqual(result.returncode, 7)
        emit_event.assert_any_call(
            "log",
            level="error",
            kind="command_failed",
            runner="copilot",
            agent="intake-agent",
            msg="intake-agent command failed (exit 7): stderr detail",
        )


class RunAgentCmdDispatchTests(unittest.TestCase):
    def test_easy__agent_cmd_returns_gemini_backend_stdout(self):
        with patch.object(run_cmds, "run_gemini_cmd", return_value="gemini output"):
            result = run_cmds.run_agent_cmd(
                runner="gemini",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
                runner_model="gemini-3-pro-preview",
            )
        self.assertEqual(result, "gemini output")

    def test_medium__agent_cmd_forwards_runner_model_as_gemini_model_kwarg(self):
        with patch.object(run_cmds, "run_gemini_cmd", return_value="gemini output") as run_gemini:
            run_cmds.run_agent_cmd(
                runner="gemini",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
                runner_model="gemini-3-pro-preview",
            )
        run_gemini.assert_called_once_with(
            prompt="Implement the task",
            agent="software-engineer-hyperagent",
            extra_skills=None,
            model="gemini-3-pro-preview",
        )

    def test_medium__agent_cmd_dispatches_to_gemini_without_model_when_runner_model_omitted(self):
        with patch.object(run_cmds, "run_gemini_cmd", return_value="gemini output") as run_gemini:
            run_cmds.run_agent_cmd(
                runner="gemini",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
            )
        run_gemini.assert_called_once_with(
            prompt="Implement the task",
            agent="software-engineer-hyperagent",
            extra_skills=None,
        )

    def test_medium__agent_cmd_forwards_stream_output_to_copilot(self):
        with patch.object(run_cmds, "run_copilot_cmd", return_value="copilot output") as run_copilot:
            run_cmds.run_agent_cmd(
                runner="copilot",
                prompt="Implement the task",
                agent="software-engineer-hyperagent",
                stream_output=True,
            )
        run_copilot.assert_called_once_with(
            prompt="Implement the task",
            agent="software-engineer-hyperagent",
            model="gpt-5-mini",
            cli_cmd="copilot",
            stream_output=True,
        )

    def test_easy__agent_cmd_rejects_unknown_runner_with_value_error(self):
        with self.assertRaisesRegex(ValueError, "copilot alias"):
            run_cmds.run_agent_cmd(runner="unknown", prompt="prompt", agent="agent")


if __name__ == "__main__":
    unittest.main()
