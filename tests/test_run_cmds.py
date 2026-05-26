"""
Tests for CLI runner behavior in core.run_cmds.

Difficulty rubric for this file:
  easy   = single-call command construction assertions.
  medium = fallback behavior across multiple Copilot CLI attempts.
  hard   = (none in this file)
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

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
    def test_easy__emit_event_inherits_current_stage(self):
        from server import events
        from server.events import read_all

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
            path = fh.name
        events._default = None
        try:
            with patch.dict(os.environ, {"AGENT_RUNNER_EVENT_LOG": path, "AGENT_RUNNER_CURRENT_STAGE": "execution"}, clear=False):
                run_cmds._emit_event("llm.call", runner="claude")

            rows = read_all(path)
            self.assertEqual(rows[0]["stage"], "execution")
        finally:
            events._default = None
            Path(path).unlink(missing_ok=True)

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


class OpenaiCompatToolRuntimeTests(unittest.TestCase):
    def _runtime(self, tmpdir: str):
        context_root = Path(tmpdir) / "agent-context"
        patcher = patch("core.run_cmds._OPENAI_COMPAT_AGENT_CONTEXT_ROOT", context_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        return run_cmds._OpenaiCompatToolRuntime(repo=None, change_id="CHANGE-1"), context_root

    def test_medium__openai_compat_tool_specs_include_protected_artifact_writers(self):
        runtime = run_cmds._OpenaiCompatToolRuntime(repo=None, change_id="CHANGE-1")
        tool_names = {spec["function"]["name"] for spec in runtime.tool_specs}

        self.assertTrue(
            {
                "write_task_plan",
                "write_assignments",
                "write_impl_report",
                "write_qa_report",
            }.issubset(tool_names)
        )
        self.assertNotIn("write_lessons_optimizer_report", tool_names)

    def test_medium__write_task_plan_serializes_parseable_yaml_and_injects_story_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_task_plan",
                    {
                        "artifact": {
                            "tasks": [
                                {
                                    "id": "T1",
                                    "title": "Render summary",
                                    "description": "Handles values with punctuation.\nFields: tasks, ACs, dependencies.",
                                    "ac_mapping": ["AC1"],
                                    "dependencies": [],
                                }
                            ]
                        }
                    },
                )
            )

            self.assertNotIn("error", result)
            self.assertEqual(result["format"], "yaml")
            report_path = context_root / "CHANGE-1" / "planning" / "tasks.yaml"
            artifact = yaml.safe_load(report_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["story_id"], "CHANGE-1")
            self.assertIn("Fields: tasks", artifact["tasks"][0]["description"])

    def test_medium__write_assignments_serializes_canonical_json_and_injects_story_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_assignments",
                    {
                        "artifact": {
                            "batches": [
                                {
                                    "batch_id": 1,
                                    "uows": [
                                        {
                                            "uow_id": "UOW-001",
                                            "source_task_id": "T1",
                                            "rationale": "Order: dependency first.",
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                )
            )

            self.assertNotIn("error", result)
            self.assertEqual(result["format"], "json")
            assignments_path = context_root / "CHANGE-1" / "planning" / "assignments.json"
            artifact = json.loads(assignments_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["story_id"], "CHANGE-1")
            self.assertEqual(artifact["batches"][0]["uows"][0]["rationale"], "Order: dependency first.")

    def test_medium__write_impl_report_serializes_colon_continuation_text_as_parseable_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_impl_report",
                    {
                        "uow_id": "UOW-004",
                        "report": {
                            "status": "complete",
                            "implementation_summary": (
                                "Verified empty handling.\n"
                                "Handles all four list fields: changed_files, commands_tests, decisions_rationale."
                            ),
                            "definition_of_done_status": [
                                {
                                    "item": "Empty fields render placeholders: no raw values",
                                    "met": True,
                                    "evidence": "pytest passed",
                                }
                            ],
                        },
                    },
                )
            )

            self.assertNotIn("error", result)
            report_path = context_root / "CHANGE-1" / "execution" / "UOW-004" / "impl_report.yaml"
            report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["uow_id"], "UOW-004")
            self.assertEqual(report["change_id"], "CHANGE-1")
            self.assertIn("fields: changed_files", report["implementation_summary"])
            self.assertEqual(result["format"], "yaml")

    def test_medium__write_impl_report_rejects_mismatched_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_impl_report",
                    {
                        "uow_id": "UOW-004",
                        "report": {
                            "uow_id": "UOW-999",
                            "change_id": "CHANGE-1",
                            "status": "complete",
                        },
                    },
                )
            )

            self.assertIn("error", result)
            self.assertIn("does not match", result["error"])
            self.assertFalse((context_root / "CHANGE-1" / "execution" / "UOW-004" / "impl_report.yaml").exists())

            result = json.loads(
                runtime.execute(
                    "write_impl_report",
                    {
                        "uow_id": "UOW-004",
                        "report": {
                            "uow_id": "UOW-004",
                            "change_id": "CHANGE-999",
                            "status": "complete",
                        },
                    },
                )
            )

            self.assertIn("error", result)
            self.assertIn("does not match", result["error"])
            self.assertFalse((context_root / "CHANGE-1" / "execution" / "UOW-004" / "impl_report.yaml").exists())

    def test_medium__write_qa_report_serializes_parseable_yaml_and_injects_story_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_qa_report",
                    {
                        "report": {
                            "qa_status": "pass",
                            "acceptance_criteria_validation": {
                                "AC1": {
                                    "status": "pass",
                                    "notes": "Evidence: pytest output.",
                                }
                            },
                            "final_recommendation": "approve",
                        }
                    },
                )
            )

            self.assertNotIn("error", result)
            report_path = context_root / "CHANGE-1" / "qa" / "qa_report.yaml"
            report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["story_id"], "CHANGE-1")
            self.assertEqual(report["acceptance_criteria_validation"]["AC1"]["notes"], "Evidence: pytest output.")

    def test_medium__protected_writers_reject_mismatched_change_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            cases = [
                ("write_task_plan", {"artifact": {"story_id": "CHANGE-999", "tasks": []}}),
                ("write_assignments", {"artifact": {"story_id": "CHANGE-999", "batches": []}}),
                ("write_qa_report", {"report": {"story_id": "CHANGE-999", "qa_status": "pass"}}),
            ]

            for tool_name, arguments in cases:
                with self.subTest(tool_name=tool_name):
                    result = json.loads(runtime.execute(tool_name, arguments))
                    self.assertIn("error", result)
                    self.assertIn("CHANGE-999", result["error"])

            self.assertFalse((context_root / "CHANGE-1" / "planning" / "tasks.yaml").exists())
            self.assertFalse((context_root / "CHANGE-1" / "planning" / "assignments.json").exists())
            self.assertFalse((context_root / "CHANGE-1" / "qa" / "qa_report.yaml").exists())

    def test_medium__write_file_rejects_protected_workflow_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            protected_paths = {
                context_root / "CHANGE-1" / "planning" / "tasks.yaml": "write_task_plan",
                context_root / "CHANGE-1" / "planning" / "assignments.json": "write_assignments",
                context_root / "CHANGE-1" / "execution" / "UOW-004" / "impl_report.yaml": "write_impl_report",
                context_root / "CHANGE-1" / "qa" / "qa_report.yaml": "write_qa_report",
            }

            for protected_path, expected_tool in protected_paths.items():
                with self.subTest(path=protected_path):
                    result = json.loads(
                        runtime.execute(
                            "write_file",
                            {
                                "path": str(protected_path),
                                "content": "status: complete\n",
                            },
                        )
                    )

                    self.assertIn("error", result)
                    self.assertIn(expected_tool, result["error"])
                    self.assertFalse(protected_path.exists())

    def test_medium__write_file_allows_unprotected_similar_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            target_path = context_root / "CHANGE-1" / "qa" / "evidence" / "logs" / "qa_report.yaml"
            result = json.loads(
                runtime.execute(
                    "write_file",
                    {
                        "path": str(target_path),
                        "content": "status: complete\n",
                    },
                )
            )

            self.assertNotIn("error", result)
            self.assertEqual(target_path.read_text(encoding="utf-8"), "status: complete\n")

    def test_medium__write_file_rejects_protected_artifact_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime, context_root = self._runtime(tmpdir)
            result = json.loads(
                runtime.execute(
                    "write_file",
                    {
                        "path": "planning/tasks.yaml",
                        "content": "tasks: []\n",
                    },
                )
            )

            self.assertIn("error", result)
            self.assertIn("write_task_plan", result["error"])
            self.assertFalse((context_root / "CHANGE-1" / "planning" / "tasks.yaml").exists())

    def test_medium__write_file_still_writes_regular_repo_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            context_root = Path(tmpdir) / "agent-context"
            with patch("core.run_cmds._OPENAI_COMPAT_AGENT_CONTEXT_ROOT", context_root):
                runtime = run_cmds._OpenaiCompatToolRuntime(repo=str(repo), change_id="CHANGE-1")
                target_file = repo / "notes" / "result.txt"
                result = json.loads(
                    runtime.execute(
                        "write_file",
                        {
                            "path": str(target_file),
                            "content": "hello\n",
                        },
                    )
                )

                self.assertNotIn("error", result)
                self.assertEqual(target_file.read_text(encoding="utf-8"), "hello\n")

    def test_medium__write_file_rejects_agent_prompt_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            context_root = Path(tmpdir) / "agent-context"
            with patch("core.run_cmds._OPENAI_COMPAT_AGENT_CONTEXT_ROOT", context_root):
                runtime = run_cmds._OpenaiCompatToolRuntime(repo=str(repo), change_id="CHANGE-1")
                protected_paths = [
                    repo / "agent-definition-source" / "intake" / "v1" / "prompt.md",
                    repo / "agent-skill-source" / "scope-and-security" / "v1" / "SKILL.md",
                    repo / "agent-script-source" / "init-artifact-dirs.py",
                    repo / ".claude" / "agents" / "intake.agent.md",
                    repo / ".codex" / "skills" / "artifact-io" / "SKILL.md",
                    repo / ".openai-compat" / "scripts" / "init-session-log.py",
                ]
                for target_file in protected_paths:
                    with self.subTest(path=target_file):
                        result = json.loads(
                            runtime.execute(
                                "write_file",
                                {
                                    "path": str(target_file),
                                    "content": "# changed\n",
                                },
                            )
                        )
                        self.assertIn("error", result)
                        self.assertIn("not allowed", result["error"])
                        self.assertFalse(target_file.exists())


class RunAgentCmdDispatchMatrixTests(unittest.TestCase):
    """For every (runner, model) in RUNNER_MODEL_CHOICES, verify run_agent_cmd
    dispatches to the correct runner function with the exact selected model."""

    @classmethod
    def setUpClass(cls):
        from core.runner_models import RUNNER_MODEL_CHOICES as _choices
        cls._choices = _choices

    def test_medium__claude_dispatch_passes_model(self):
        for model in self._choices["claude"]:
            with self.subTest(model=model):
                with patch("core.run_cmds.run_claude_cmd", return_value="OK") as run_fn:
                    result = run_cmds.run_agent_cmd(
                        runner="claude",
                        prompt="Say OK",
                        agent="qa-evaluator",
                        runner_model=model,
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)

    def test_medium__copilot_dispatch_passes_model(self):
        for model in self._choices["copilot"]:
            with self.subTest(model=model):
                with patch("core.run_cmds.run_copilot_cmd", return_value="OK") as run_fn:
                    result = run_cmds.run_agent_cmd(
                        runner="copilot",
                        prompt="Say OK",
                        agent="qa-evaluator",
                        runner_model=model,
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)
                self.assertEqual(run_fn.call_args.kwargs.get("cli_cmd"), "copilot")

    def test_medium__codex_dispatch_passes_model(self):
        for model in self._choices["codex"]:
            with self.subTest(model=model):
                with patch("core.run_cmds.run_codex_cmd", return_value="OK") as run_fn:
                    result = run_cmds.run_agent_cmd(
                        runner="codex",
                        prompt="Say OK",
                        agent="qa-evaluator",
                        runner_model=model,
                        repo="/tmp/repo",
                        change_id="CHANGE-1",
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)
                self.assertEqual(run_fn.call_args.kwargs.get("repo"), "/tmp/repo")
                self.assertEqual(run_fn.call_args.kwargs.get("change_id"), "CHANGE-1")

    def test_medium__gemini_dispatch_passes_model(self):
        for model in self._choices["gemini"]:
            with self.subTest(model=model):
                with patch("core.run_cmds.run_gemini_cmd", return_value="OK") as run_fn:
                    result = run_cmds.run_agent_cmd(
                        runner="gemini",
                        prompt="Say OK",
                        agent="qa-evaluator",
                        runner_model=model,
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)

    def test_medium__openai_compat_dispatch_passes_model(self):
        for model in self._choices["openai-compat"]:
            with self.subTest(model=model):
                with patch("core.run_cmds.run_openai_compat_cmd", return_value="OK") as run_fn:
                    result = run_cmds.run_agent_cmd(
                        runner="openai-compat",
                        prompt="Say OK",
                        agent="qa-evaluator",
                        runner_model=model,
                        repo="/tmp/repo",
                        change_id="CHANGE-1",
                    )
                self.assertEqual(result, "OK")
                run_fn.assert_called_once()
                self.assertEqual(run_fn.call_args.kwargs.get("model"), model)
                self.assertEqual(run_fn.call_args.kwargs.get("runner"), "openai-compat")
                self.assertEqual(run_fn.call_args.kwargs.get("repo"), "/tmp/repo")
                self.assertEqual(run_fn.call_args.kwargs.get("change_id"), "CHANGE-1")


class RunnerCommandPayloadMatrixTests(unittest.TestCase):
    """For every model in RUNNER_MODEL_CHOICES, verify the lower-level runner
    function constructs the correct CLI command or API payload with that model."""

    @classmethod
    def setUpClass(cls):
        from core.runner_models import RUNNER_MODEL_CHOICES as _choices
        cls._choices = _choices

    # -- claude ----------------------------------------------------------------

    def test_medium__claude_includes_model_in_command(self):
        for model in self._choices["claude"]:
            with self.subTest(model=model):
                fake_result = subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=json.dumps({
                        "result": "OK",
                        "total_input_tokens": 1,
                        "total_output_tokens": 1,
                        "cost_usd": 0.0,
                    }),
                    stderr="",
                )
                with patch("core.run_cmds._run_cli", return_value=fake_result) as run_cli:
                    result = run_cmds.run_claude_cmd(
                        prompt="Say OK",
                        agent="qa-evaluator",
                        model=model,
                    )
                self.assertEqual(result, "OK")
                run_cli.assert_called_once()
                cmd = run_cli.call_args.args[0]
                self.assertIn("--model", cmd)
                model_idx = cmd.index("--model")
                self.assertLess(model_idx + 1, len(cmd))
                self.assertEqual(cmd[model_idx + 1], model)

    # -- copilot ---------------------------------------------------------------

    def test_medium__copilot_includes_model_in_command(self):
        for model in self._choices["copilot"]:
            with self.subTest(model=model):
                fake_result = subprocess.CompletedProcess(
                    args=["copilot"],
                    returncode=0,
                    stdout="OK",
                    stderr="",
                )
                with (
                    patch.dict(run_cmds._COPILOT_EMBEDDED_AGENT_FALLBACK, {}, clear=True),
                    patch("core.run_cmds._run_cli", return_value=fake_result) as run_cli,
                ):
                    result = run_cmds.run_copilot_cmd(
                        prompt="Say OK",
                        agent="qa-evaluator",
                        model=model,
                        cli_cmd="copilot",
                    )
                self.assertEqual(result, "OK")
                run_cli.assert_called_once()
                cmd = run_cli.call_args.args[0]
                self.assertIn("--model", cmd)
                model_idx = cmd.index("--model")
                self.assertLess(model_idx + 1, len(cmd))
                self.assertEqual(cmd[model_idx + 1], model)

    # -- gemini ----------------------------------------------------------------

    def test_medium__gemini_includes_model_in_command(self):
        for model in self._choices["gemini"]:
            with self.subTest(model=model):
                fake_result = subprocess.CompletedProcess(
                    args=["gemini"],
                    returncode=0,
                    stdout="OK",
                    stderr="",
                )
                with (
                    patch("core.run_cmds._build_gemini_prompt", return_value="SYSTEM\n\nSay OK"),
                    patch("core.run_cmds._run_cli", return_value=fake_result) as run_cli,
                ):
                    result = run_cmds.run_gemini_cmd(
                        prompt="Say OK",
                        agent="qa-evaluator",
                        model=model,
                    )
                self.assertEqual(result, "OK")
                run_cli.assert_called_once()
                cmd = run_cli.call_args.args[0]
                self.assertIn("--model", cmd)
                model_idx = cmd.index("--model")
                self.assertLess(model_idx + 1, len(cmd))
                self.assertEqual(cmd[model_idx + 1], model)

    # -- codex -----------------------------------------------------------------

    def test_medium__codex_includes_model_and_workspace_sandbox_in_command(self):
        for model in self._choices["codex"]:
            with self.subTest(model=model):
                fake_result = subprocess.CompletedProcess(
                    args=["codex"],
                    returncode=0,
                    stdout="OK from stdout",
                    stderr="",
                )
                with (
                    tempfile.TemporaryDirectory() as tmpdir,
                    patch("core.run_cmds._build_codex_prompt", return_value="SYSTEM\n\nSay OK"),
                    patch("core.run_cmds._run_cli", return_value=fake_result) as run_cli,
                ):
                    result = run_cmds.run_codex_cmd(
                        prompt="Say OK",
                        agent="qa-evaluator",
                        model=model,
                        repo=tmpdir,
                    )
                self.assertEqual(result, "OK from stdout")
                run_cli.assert_called_once()
                cmd = run_cli.call_args.args[0]
                self.assertEqual(cmd[:2], ["codex", "exec"])
                self.assertIn("--model", cmd)
                self.assertEqual(cmd[cmd.index("--model") + 1], model)
                self.assertIn("--sandbox", cmd)
                self.assertEqual(cmd[cmd.index("--sandbox") + 1], "workspace-write")
                self.assertIn("--ask-for-approval", cmd)
                self.assertEqual(cmd[cmd.index("--ask-for-approval") + 1], "never")
                self.assertIn("--output-last-message", cmd)
                self.assertEqual(cmd[-1], "SYSTEM\n\nSay OK")

    # -- openai-compat ---------------------------------------------------------

    def test_medium__openai_compat_passes_model_to_chat_api(self):
        for model in self._choices["openai-compat"]:
            with self.subTest(model=model):
                chat_response = {
                    "message": {"role": "assistant", "content": "OK"},
                }
                with (
                    tempfile.TemporaryDirectory() as tmpdir,
                    patch("core.run_cmds.build_runner_agent_instructions", return_value="AGENT SPEC"),
                    patch("core.run_cmds._openai_compat_show_capabilities", return_value=["completion", "tools"]),
                    patch("core.run_cmds._openai_compat_chat", return_value=chat_response) as chat_fn,
                ):
                    result = run_cmds.run_openai_compat_cmd(
                        prompt="Say OK",
                        agent="qa-evaluator",
                        model=model,
                        runner="openai-compat",
                        repo=tmpdir,
                        change_id="CHANGE-1",
                    )
                self.assertEqual(result, "OK")
                chat_fn.assert_called()
                self.assertEqual(chat_fn.call_args.kwargs.get("model"), model)


if __name__ == "__main__":
    unittest.main()
