"""
Tests for CLI runner behavior in core.run_cmds.

Difficulty rubric for this file:
  easy   = single-call command construction assertions.
  medium = fallback behavior across multiple Copilot CLI attempts.
  hard   = (none in this file)
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.run_cmds as run_cmds


class CopilotEmbeddedAgentFallbackTests(unittest.TestCase):
    def test_medium__custom_agent_refusal_retries_with_embedded_agent_prompt(self):
        with (
            patch.dict(run_cmds._COPILOT_EMBEDDED_AGENT_FALLBACK, {}, clear=True),
            patch("core.run_cmds.build_runner_agent_instructions", return_value="AGENT SPEC"),
            patch(
                "core.run_cmds._run_cli",
                side_effect=[
                    subprocess.CompletedProcess(
                        args=["copilot"],
                        returncode=0,
                        stdout="I'm sorry, but I cannot assist with that request.",
                        stderr="",
                    ),
                    subprocess.CompletedProcess(
                        args=["copilot"],
                        returncode=0,
                        stdout="task plan written",
                        stderr="",
                    ),
                ],
            ) as run_cli,
        ):
            result = run_cmds.run_copilot_cmd(
                prompt="Generate a task plan.",
                agent="task-generator",
                model="gpt-5-mini",
                cli_cmd="copilot",
            )

        self.assertEqual(result, "task plan written")
        self.assertEqual(run_cli.call_count, 2)

        first_cmd = run_cli.call_args_list[0].args[0]
        second_cmd = run_cli.call_args_list[1].args[0]
        self.assertIn("--agent=task-generator", first_cmd)
        self.assertTrue(all(not arg.startswith("--agent=") for arg in second_cmd))
        embedded_prompt = second_cmd[second_cmd.index("-p") + 1]
        self.assertIn("## Agent specification", embedded_prompt)
        self.assertIn("AGENT SPEC", embedded_prompt)
        self.assertIn("## Task to execute", embedded_prompt)

    def test_easy__embedded_fallback_mode_skips_custom_agent_flag_on_subsequent_calls(self):
        with (
            patch.dict(run_cmds._COPILOT_EMBEDDED_AGENT_FALLBACK, {"copilot": True}, clear=True),
            patch("core.run_cmds.build_runner_agent_instructions", return_value="AGENT SPEC"),
            patch(
                "core.run_cmds._run_cli",
                return_value=subprocess.CompletedProcess(
                    args=["copilot"],
                    returncode=0,
                    stdout="ok",
                    stderr="",
                ),
            ) as run_cli,
        ):
            result = run_cmds.run_copilot_cmd(
                prompt="Evaluate the task plan.",
                agent="task-plan-evaluator",
                model="gpt-5-mini",
                cli_cmd="copilot",
            )

        self.assertEqual(result, "ok")
        cmd = run_cli.call_args.args[0]
        self.assertTrue(all(not arg.startswith("--agent=") for arg in cmd))


class CliSessionLogTests(unittest.TestCase):
    def test_easy__run_cli_writes_per_agent_session_log_when_change_id_is_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_root = Path(tmpdir) / "logs"
            result = subprocess.CompletedProcess(
                args=["agent-cli"],
                returncode=0,
                stdout="completed successfully",
                stderr="",
            )
            with (
                patch.object(run_cmds, "_RUNNER_LOGS_ROOT", logs_root),
                patch.dict(
                    run_cmds.os.environ,
                    {
                        "AGENT_RUNNER_EVENT_LOG": "/tmp/events.jsonl",
                        "AGENT_RUNNER_CHANGE_ID": "CHANGE-1",
                        "AGENT_RUNNER_CURRENT_STAGE": "execution",
                    },
                    clear=False,
                ),
            ):
                run_cmds._write_cli_session_log(
                    runner="claude",
                    agent="software-engineer-hyperagent",
                    cmd=["claude", "-p", "hidden prompt"],
                    result=result,
                    duration_ms=1234,
                    model="claude-sonnet",
                    prompt_text="hidden prompt",
                    attempt=1,
                    max_attempts=1,
                )

            log_files = list((logs_root / "CHANGE-1" / "software-engineer-hyperagent").glob("*_session.json"))
            self.assertEqual(len(log_files), 1)
            payload = json.loads(log_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["change_id"], "CHANGE-1")
            self.assertEqual(payload["stage"], "execution")
            self.assertEqual(payload["agent"], "software-engineer-hyperagent")
            self.assertEqual(payload["model"], "claude-sonnet")
            self.assertEqual(payload["attempt"], 1)
            self.assertEqual(payload["cmd"], ["claude"])
            self.assertEqual(payload["prompt_text"], "hidden prompt")
            self.assertEqual(payload["stdout_tail"], "completed successfully")
            self.assertEqual(payload["response_text"], "completed successfully")
            self.assertEqual(payload["prompt_est_tokens"], 3)


class LlmCallOpikTelemetryTests(unittest.TestCase):
    def test_medium__llm_call_event_updates_opik_with_usage_metadata_without_raw_prompt(self):
        with (
            patch("core.run_cmds._emit_event") as emit_event,
            patch("core.run_cmds.opik_context.get_current_span_data", return_value=object()),
            patch("core.run_cmds.opik_context.get_current_trace_data", return_value=object()),
            patch("core.run_cmds.opik_context.update_current_span") as update_span,
            patch("core.run_cmds.opik_context.update_current_trace") as update_trace,
        ):
            run_cmds._emit_llm_call_event(
                runner="claude",
                agent="task-generator",
                model="claude-sonnet",
                status="ok",
                duration_ms=1234,
                prompt_text="secret prompt body",
                response_text="private response body",
                prompt_tokens=10,
                completion_tokens=5,
                cost_usd=0.02,
                response_parse_ok=True,
            )

        update_span.assert_called_once()
        span_kwargs = update_span.call_args.kwargs
        self.assertEqual(span_kwargs["usage"], {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        self.assertEqual(span_kwargs["metadata"]["llm_agent"], "task-generator")
        self.assertEqual(span_kwargs["metadata"]["llm_duration_ms"], 1234)
        self.assertEqual(span_kwargs["total_cost"], 0.02)
        self.assertNotIn("secret prompt body", json.dumps(span_kwargs, default=str))
        self.assertNotIn("private response body", json.dumps(span_kwargs, default=str))
        self.assertEqual(span_kwargs["input"]["prompt_chars"], len("secret prompt body"))
        self.assertEqual(span_kwargs["output"]["response_chars"], len("private response body"))
        self.assertEqual(span_kwargs["feedback_scores"][0]["name"], "llm_call_success")

        update_trace.assert_called_once()
        self.assertNotIn("feedback_scores", update_trace.call_args.kwargs)
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.args[0], "llm.call")
        self.assertEqual(emit_event.call_args.kwargs["tokens_in"], 10)

    def test_medium__llm_call_event_skips_opik_when_no_active_context(self):
        with (
            patch("core.run_cmds._emit_event") as emit_event,
            patch("core.run_cmds.opik_context.get_current_span_data", return_value=None),
            patch("core.run_cmds.opik_context.get_current_trace_data", return_value=None),
            patch("core.run_cmds.opik_context.update_current_span") as update_span,
            patch("core.run_cmds.opik_context.update_current_trace") as update_trace,
        ):
            run_cmds._emit_llm_call_event(
                runner="copilot",
                agent="qa-engineer",
                model=None,
                status="ok",
                duration_ms=25,
                prompt_text="prompt",
                response_text="response",
            )

        update_span.assert_not_called()
        update_trace.assert_not_called()
        emit_event.assert_called_once()


class OpenaiCompatRunnerTests(unittest.TestCase):
    def test_easy__run_agent_cmd_routes_openai_compat_alias_to_openai_compat_runner(self):
        with (
            patch(
                "server.config.load_config",
                return_value={"runner_aliases": {"ds4": {"provider": "openai-compat", "model": "deepseek-v4-pro:cloud"}}},
            ),
            patch("core.run_cmds.run_openai_compat_cmd", return_value="ok") as run_openai_compat,
        ):
            result = run_cmds.run_agent_cmd(
                runner="ds4",
                prompt="Create intake artifacts.",
                agent="intake",
                runner_model="openai-compat/deepseek-v4-pro:cloud",
                repo="/tmp/repo",
                change_id="CHANGE-1",
            )

        self.assertEqual(result, "ok")
        run_openai_compat.assert_called_once()
        self.assertEqual(run_openai_compat.call_args.kwargs["runner"], "ds4")
        self.assertEqual(run_openai_compat.call_args.kwargs["repo"], "/tmp/repo")
        self.assertEqual(run_openai_compat.call_args.kwargs["change_id"], "CHANGE-1")

    def test_medium__openai_compat_tool_loop_writes_file_and_returns_final_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            target_file = repo / "notes" / "result.txt"
            responses = [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "write_file",
                                    "arguments": {
                                        "path": str(target_file),
                                        "content": "hello from openai-compat\n",
                                    },
                                }
                            }
                        ],
                    },
                    "prompt_eval_count": 10,
                    "eval_count": 20,
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": "Done.",
                    },
                    "prompt_eval_count": 5,
                    "eval_count": 7,
                },
            ]
            with (
                patch("core.run_cmds.build_runner_agent_instructions", return_value="AGENT SPEC"),
                patch("core.run_cmds._openai_compat_show_capabilities", return_value=["completion", "tools"]),
                patch("core.run_cmds._openai_compat_chat", side_effect=responses),
            ):
                result = run_cmds.run_openai_compat_cmd(
                    prompt="Write the file and finish.",
                    agent="software-engineer-hyperagent",
                    model="openai-compat/deepseek-v4-pro:cloud",
                    runner="openai-compat",
                    repo=str(repo),
                    change_id="CHANGE-1",
                )
                self.assertEqual(result, "Done.")
                self.assertTrue(target_file.is_file())
                self.assertEqual(target_file.read_text(encoding="utf-8"), "hello from openai-compat\n")


if __name__ == "__main__":
    unittest.main()
