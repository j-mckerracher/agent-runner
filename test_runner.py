#!/usr/bin/env python3
"""Unit tests for the refactored agent-runner package."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

RUNNER_DIR = Path(__file__).resolve().parent
REPO_ROOT = RUNNER_DIR.parent
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

from agent_runner import agents as agents_module
from agent_runner import commands as commands_module
from agent_runner import models
from agent_runner import runtime as runtime_module
from agent_runner.cli import general as general_cli
from agent_runner.cli import interactive as interactive_cli
from agent_runner.integrations import ado as ado_module
from agent_runner.integrations import discord_resume
from agent_runner.workflow import stages as stages_module
from agent_runner.workflow.engine import run_execution_loop, run_workflow

BRIDGE_MODULE_PATH = REPO_ROOT / ".claude" / "scripts" / "discord_escalation_bridge.py"
bridge_spec = importlib.util.spec_from_file_location(
    "discord_escalation_bridge",
    BRIDGE_MODULE_PATH,
)
if bridge_spec is None or bridge_spec.loader is None:
    raise RuntimeError(f"Unable to load module from {BRIDGE_MODULE_PATH}")
bridge_module = importlib.util.module_from_spec(bridge_spec)
sys.modules[bridge_spec.name] = bridge_module
bridge_spec.loader.exec_module(bridge_module)


def make_config(artifact_root: Path, **overrides) -> models.WorkflowConfig:
    config = models.WorkflowConfig(
        repo_root=REPO_ROOT,
        workflow_assets_root=models.WORKFLOW_ASSETS_ROOT,
        change_id="WI-TEST",
        context="Workflow context",
        artifact_root=artifact_root,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_event(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


class DiscoverAgentsTests(unittest.TestCase):
    def test_discover_agents_indexes_numbered_and_named_agents(self) -> None:
        agents = agents_module.discover_agents(models.WORKFLOW_ASSETS_ROOT)
        numbered = agents["01-intake"]
        named = agents["intake-agent"]
        self.assertEqual(numbered.path, named.path)
        self.assertEqual(numbered.name, "intake-agent")


class StageSpecTests(unittest.TestCase):
    def test_loop_stage_specs_centralize_core_stage_order(self) -> None:
        self.assertEqual(
            [spec.stage_name for spec in stages_module.LOOP_STAGE_SPECS],
            ["task_generator", "task_assigner", "qa"],
        )
        self.assertEqual(stages_module.TOTAL_WORKFLOW_STAGES, 6)


class EvaluationHelpersTests(unittest.TestCase):
    def test_read_evaluation_result_reports_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "eval.json"
            path.write_text(
                json.dumps({"overall_result": "pass", "issues": []}),
                encoding="utf-8",
            )
            passed, payload = runtime_module.read_evaluation_result(path)

        self.assertTrue(passed)
        self.assertEqual(payload["overall_result"], "pass")


class BackendCommandTests(unittest.TestCase):
    def test_build_agent_command_for_copilot_uses_agent_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = make_config(
                Path(tmp_dir),
                cli_backend="copilot",
                cli_bin="copilot",
                model="gpt-5.4",
                additional_dirs=[Path(tmp_dir) / "extra"],
            )
            agent = models.AgentSpec(
                key="04-software-engineer-hyperagent",
                name="software-engineer-hyperagent",
                description="test agent",
                path=Path(tmp_dir) / "agent.md",
            )

            command = agents_module.build_agent_command(config, agent, "test prompt")

        self.assertEqual(command[:3], ["copilot", "-p", "test prompt"])
        self.assertIn("--agent", command)
        self.assertEqual(command[command.index("--agent") + 1], agent.key)
        self.assertIn("--allow-all", command)
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.4")

    def test_build_agent_command_for_claude_uses_agent_name_and_prompt_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = make_config(
                Path(tmp_dir),
                cli_backend="claude",
                cli_bin="claude",
                model="sonnet",
            )
            agent = models.AgentSpec(
                key="04-software-engineer-hyperagent",
                name="software-engineer-hyperagent",
                description="test agent",
                path=Path(tmp_dir) / "agent.md",
            )

            command = agents_module.build_agent_command(config, agent, "test prompt")

        self.assertEqual(command[0], "claude")
        self.assertIn("--print", command)
        self.assertEqual(command[command.index("--agent") + 1], agent.name)
        self.assertEqual(
            command[command.index("--permission-mode") + 1],
            "bypassPermissions",
        )
        self.assertEqual(command[-1], "test prompt")


class GeneralCliBuilderTests(unittest.TestCase):
    def test_general_cli_backend_builders_keep_backend_specific_flags(self) -> None:
        args = mock.Mock(model="gpt-5.4", agent="spike", prompt="hello")
        copilot = general_cli._build_github_copilot_cmd(args)
        claude = general_cli._build_claude_code_cmd(args)

        self.assertIn("--allow-all-tools", copilot)
        self.assertIn("--dangerously-skip-permissions", claude)
        self.assertEqual(copilot[-1], "hello")
        self.assertEqual(claude[-1], "hello")


class WorkItemReferenceTests(unittest.TestCase):
    def test_parse_work_item_reference_accepts_bare_id(self) -> None:
        reference = ado_module.parse_work_item_reference(
            "4461550",
            default_organization="https://dev.azure.com/mclm",
            default_project="Mayo Collaborative Services",
        )

        self.assertEqual(reference.organization_url, "https://dev.azure.com/mclm")
        self.assertEqual(reference.project, "Mayo Collaborative Services")
        self.assertEqual(reference.work_item_id, "4461550")

    def test_parse_work_item_reference_accepts_full_url(self) -> None:
        reference = ado_module.parse_work_item_reference(
            "https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/4461550",
            default_organization="https://dev.azure.com/ignored",
            default_project="ignored",
        )

        self.assertEqual(reference.organization_url, "https://dev.azure.com/mclm")
        self.assertEqual(reference.project, "Mayo Collaborative Services")
        self.assertEqual(reference.work_item_id, "4461550")

    def test_build_ado_context_extracts_embedded_acceptance_criteria(self) -> None:
        reference = models.WorkItemReference(
            organization_url="https://dev.azure.com/mclm",
            project="Mayo Collaborative Services",
            work_item_id="4461550",
        )
        payload = {
            "fields": {
                "System.Title": "Do not prompt twice",
                "System.Description": (
                    "<p>As a user, I do not want to be asked more than once per session.</p>"
                    "<p>Acceptance Criteria:</p>"
                    "<ul><li>AC - cache the first reason</li>"
                    "<li>AC - reset after save</li></ul>"
                ),
                "System.AreaPath": "Area Path",
            }
        }

        context = ado_module.build_ado_context(payload, reference)

        self.assertIn("Do not prompt twice", context)
        self.assertIn("Acceptance Criteria:", context)
        self.assertIn("- cache the first reason", context)
        self.assertIn("- reset after save", context)
        self.assertIn("Azure DevOps Work Item ID: 4461550", context)


class InteractiveConfigTests(unittest.TestCase):
    def test_collect_interactive_config_prints_robot_banner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_root = Path(tmp_dir)
            for path in [
                artifact_root / "WI-4461550" / "intake" / "story.yaml",
                artifact_root / "WI-4461550" / "intake" / "config.yaml",
                artifact_root / "WI-4461550" / "intake" / "constraints.md",
            ]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")

            responses = iter(["2"])
            stdout = io.StringIO()
            with (
                redirect_stdout(stdout),
                mock.patch.object(
                    interactive_cli,
                    "select_backend",
                    return_value=models.BackendSpec(
                        key="copilot",
                        label="GitHub Copilot",
                        command="copilot",
                    ),
                ),
            ):
                interactive_cli.collect_interactive_config(
                    input_fn=lambda prompt: next(responses),
                    require_tty=False,
                    repo_root=REPO_ROOT,
                    artifact_root=artifact_root,
                )

        self.assertIn("[::]", stdout.getvalue())
        self.assertIn(".-:||:-.", stdout.getvalue())

    def test_collect_interactive_config_can_reuse_existing_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_root = Path(tmp_dir)
            for path in [
                artifact_root / "WI-4461550" / "intake" / "story.yaml",
                artifact_root / "WI-4461550" / "intake" / "config.yaml",
                artifact_root / "WI-4461550" / "intake" / "constraints.md",
            ]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")

            responses = iter(["2"])
            with mock.patch.object(
                interactive_cli,
                "select_backend",
                return_value=models.BackendSpec(
                    key="copilot",
                    label="GitHub Copilot",
                    command="copilot",
                ),
            ):
                config = interactive_cli.collect_interactive_config(
                    input_fn=lambda prompt: next(responses),
                    require_tty=False,
                    repo_root=REPO_ROOT,
                    artifact_root=artifact_root,
                )

        self.assertEqual(config.change_id, "WI-4461550")
        self.assertTrue(config.reuse_existing_intake)
        self.assertEqual(config.context, "")

    def test_collect_interactive_config_can_fetch_context_from_ado(self) -> None:
        responses = iter(["1", "4461550"])
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            mock.patch.object(
                interactive_cli,
                "select_backend",
                return_value=models.BackendSpec(
                    key="claude",
                    label="Claude Code",
                    command="claude",
                ),
            ),
            mock.patch.object(
                interactive_cli,
                "resolve_ado_defaults",
                return_value=(
                    "https://dev.azure.com/mclm",
                    "Mayo Collaborative Services",
                ),
            ),
            mock.patch.object(
                interactive_cli,
                "fetch_ado_context",
                return_value="Fetched ADO context",
            ),
        ):
            config = interactive_cli.collect_interactive_config(
                input_fn=lambda prompt: next(responses),
                require_tty=False,
                repo_root=REPO_ROOT,
                artifact_root=Path(tmp_dir),
            )

        self.assertEqual(config.cli_backend, "claude")
        self.assertEqual(config.change_id, "WI-4461550")
        self.assertEqual(config.context, "Fetched ADO context")
        self.assertFalse(config.reuse_existing_intake)


class RunCommandTests(unittest.TestCase):
    def test_run_command_returns_timeout_result_with_partial_output(self) -> None:
        command = [
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "print('partial stdout'); "
                "print('partial stderr', file=sys.stderr); "
                "sys.stdout.flush(); sys.stderr.flush(); "
                "time.sleep(2)"
            ),
        ]

        result = commands_module.run_command(
            command,
            cwd=REPO_ROOT,
            timeout_seconds=1,
            heartbeat_interval=1,
        )

        self.assertEqual(result.exit_code, 124)
        self.assertIn("partial stdout", result.stdout)
        self.assertIn("partial stderr", result.stderr)
        self.assertIn("Command timed out after 1 seconds.", result.stderr)


class ExecutionLoopRetryTests(unittest.TestCase):
    def test_run_execution_loop_retries_after_engineer_command_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_root = Path(tmp_dir)
            base = artifact_root / "WI-TEST"
            planning_dir = base / "planning"
            intake_dir = base / "intake"
            execution_dir = base / "execution" / "UOW-001"
            planning_dir.mkdir(parents=True)
            intake_dir.mkdir(parents=True)
            execution_dir.mkdir(parents=True)

            (planning_dir / "assignments.json").write_text(
                json.dumps(
                    {
                        "story_id": "WI-TEST",
                        "batches": [
                            {
                                "batch_id": 1,
                                "batch": 1,
                                "parallel_execution": False,
                                "uows": [
                                    {
                                        "uow_id": "UOW-001",
                                        "assigned_role": "software-engineer",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (planning_dir / "tasks.yaml").write_text("tasks: []\n", encoding="utf-8")
            (intake_dir / "story.yaml").write_text("story_id: WI-TEST\n", encoding="utf-8")

            config = make_config(artifact_root, max_implementation_attempts=3)
            software_engineer = models.AgentSpec(
                key="04-software-engineer-hyperagent",
                name="software-engineer-hyperagent",
                description="test engineer",
                path=Path(tmp_dir) / "04-software-engineer-hyperagent.agent.md",
            )
            implementation_evaluator = models.AgentSpec(
                key="08-implementation-evaluator",
                name="implementation-evaluator",
                description="test evaluator",
                path=Path(tmp_dir) / "08-implementation-evaluator.agent.md",
            )
            agents = {
                "04-software-engineer-hyperagent": software_engineer,
                "software-engineer-hyperagent": software_engineer,
                "08-implementation-evaluator": implementation_evaluator,
                "implementation-evaluator": implementation_evaluator,
            }

            call_log: list[tuple[str, int, str | None, bool, str]] = []

            def fake_invoke_agent(
                config,
                agent,
                prompt,
                stage_key,
                attempt,
                uow_id=None,
                raise_on_error=True,
                **kwargs,
            ):
                call_log.append((stage_key, attempt, uow_id, raise_on_error, prompt))
                if stage_key == "software_engineer" and attempt == 1:
                    return models.CommandResult(
                        command=["copilot", "-p", "attempt-1"],
                        exit_code=124,
                        stdout="",
                        stderr="Command timed out after 1800 seconds.",
                    )
                if stage_key == "software_engineer" and attempt == 2:
                    (execution_dir / "uow_spec.yaml").write_text(
                        "uow_id: UOW-001\n",
                        encoding="utf-8",
                    )
                    (execution_dir / "impl_report.yaml").write_text(
                        "definition_of_done_status: []\n",
                        encoding="utf-8",
                    )
                    return models.CommandResult(
                        command=["copilot", "-p", "attempt-2"],
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                    )
                if stage_key == "implementation_evaluator" and attempt == 2:
                    (execution_dir / "eval_impl_2.json").write_text(
                        json.dumps(
                            {"overall_result": "pass", "score": 95, "issues": []}
                        ),
                        encoding="utf-8",
                    )
                    return models.CommandResult(
                        command=["copilot", "-p", "eval-2"],
                        exit_code=0,
                        stdout="pass",
                        stderr="",
                    )
                self.fail(
                    f"Unexpected invoke_agent call: stage={stage_key}, attempt={attempt}"
                )

            with mock.patch(
                "agent_runner-pkg.workflow.engine.invoke_agent",
                side_effect=fake_invoke_agent,
            ):
                result = run_execution_loop(config, agents)

        self.assertTrue(result.passed)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(
            [(stage_key, attempt) for stage_key, attempt, _, _, _ in call_log],
            [
                ("software_engineer", 1),
                ("software_engineer", 2),
                ("implementation_evaluator", 2),
            ],
        )
        self.assertIn("Runner-captured failure details", call_log[1][4])
        self.assertIn("Command timed out after 1800 seconds.", call_log[1][4])


class DryRunWorkflowTests(unittest.TestCase):
    def test_run_workflow_dry_run_completes_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            config = make_config(Path(artifact_root), change_id="WI-DRY-RUN", dry_run=True)

            results = run_workflow(config)

            self.assertTrue(all(result.passed for result in results))
            base = Path(artifact_root) / "WI-DRY-RUN"
            self.assertTrue((base / "intake" / "story.yaml").is_file())
            self.assertTrue((base / "planning" / "assignments.json").is_file())
            self.assertTrue(
                (base / "execution" / "UOW-001" / "impl_report.yaml").is_file()
            )
            self.assertTrue((base / "qa" / "qa_report.yaml").is_file())
            self.assertTrue(
                (base / "summary" / "lessons_optimizer_report.yaml").is_file()
            )
            self.assertTrue((base / "logs" / "workflow_runner").is_dir())


class ObservabilityTests(unittest.TestCase):
    def test_run_workflow_emits_events_to_observability_sink(self) -> None:
        with tempfile.TemporaryDirectory() as artifact_root:
            sink = FakeSink()
            config = make_config(
                Path(artifact_root),
                change_id="WI-OBS",
                dry_run=True,
                observability_sink=sink,
            )

            run_workflow(config)

        event_types = [event_type for event_type, _ in sink.events]
        self.assertIn("workflow_start", event_types)
        self.assertIn("stage_start", event_types)
        self.assertIn("agent_dispatch", event_types)
        self.assertIn("agent_result", event_types)
        self.assertIn("workflow_complete", event_types)


class MainCliTests(unittest.TestCase):
    def test_main_rejects_legacy_cli_arguments(self) -> None:
        exit_code = interactive_cli.main(["--change-id", "WI-TEST"])
        self.assertEqual(exit_code, 2)


class DiscordBridgeParserTests(unittest.TestCase):
    def test_is_resume_message_detects_prefix(self) -> None:
        self.assertTrue(bridge_module.is_resume_message("RESUME: some answer"))
        self.assertTrue(bridge_module.is_resume_message("resume: lowercase"))
        self.assertFalse(bridge_module.is_resume_message("Not a resume message"))
        self.assertFalse(bridge_module.is_resume_message("RE SUME: broken"))

    def test_parse_resume_message_extracts_key_value_pairs(self) -> None:
        result = bridge_module.parse_resume_message(
            "RESUME: Q1=first answer, Q2=second answer",
            ["Question 1", "Question 2"],
        )
        self.assertEqual(result["answers"]["Q1"], "first answer")
        self.assertEqual(result["answers"]["Q2"], "second answer")

    def test_parse_resume_message_multiline_key_values(self) -> None:
        result = bridge_module.parse_resume_message(
            "RESUME:\nQ1=line one answer\nQ2=line two answer",
            ["Question 1", "Question 2"],
        )
        self.assertEqual(result["answers"]["Q1"], "line one answer")
        self.assertEqual(result["answers"]["Q2"], "line two answer")

    def test_parse_resume_message_free_form_maps_to_q1(self) -> None:
        result = bridge_module.parse_resume_message(
            "RESUME: just a free-form reply here",
            ["Some question"],
        )
        self.assertEqual(result["answers"]["Q1"], "just a free-form reply here")

    def test_parse_resume_message_no_questions_captures_raw(self) -> None:
        result = bridge_module.parse_resume_message("RESUME: free text", [])
        self.assertEqual(result["raw"], "RESUME: free text")

    def test_check_missing_answers_detects_gaps(self) -> None:
        missing = bridge_module.check_missing_answers(
            {"Q1": "answer"},
            ["Question 1", "Question 2"],
        )
        self.assertEqual(len(missing), 1)
        self.assertIn("Q2", missing[0])

    def test_check_missing_answers_all_present(self) -> None:
        missing = bridge_module.check_missing_answers(
            {"Q1": "a", "Q2": "b"},
            ["Question 1", "Question 2"],
        )
        self.assertEqual(missing, [])


class DiscordBridgeEscalationMessageTests(unittest.TestCase):
    def test_build_escalation_message_includes_key_fields(self) -> None:
        escalation = {
            "stage_key": "task_generator",
            "reason": "Ambiguous requirements",
            "blocking_questions": ["What is the scope?", "What is the deadline?"],
        }
        msg = bridge_module.build_escalation_message(
            escalation,
            "WI-9999",
            Path("/status/escalated.json"),
        )
        self.assertIn("WI-9999", msg)
        self.assertIn("task_generator", msg)
        self.assertIn("Ambiguous requirements", msg)
        self.assertIn("What is the scope?", msg)
        self.assertIn("RESUME:", msg)


class DiscordBridgeDryRunTests(unittest.TestCase):
    def test_dry_run_creates_resume_json_from_simulated_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            status_dir = Path(tmp_dir) / "status"
            status_dir.mkdir()
            escalated_path = status_dir / "escalated.json"
            escalated_path.write_text(
                json.dumps(
                    {
                        "stage_key": "task_generator",
                        "reason": "Test escalation",
                        "blocking_questions": ["What to do?"],
                    }
                ),
                encoding="utf-8",
            )

            def _write() -> None:
                import time as _time

                _time.sleep(0.1)
                (status_dir / "discord_simulated_message.txt").write_text(
                    "RESUME: Q1=do the thing",
                    encoding="utf-8",
                )

            threading.Thread(target=_write, daemon=True).start()
            result_payload = bridge_module.run_dry_run_loop(status_dir, ["What to do?"])

        self.assertIsNotNone(result_payload)
        self.assertEqual(result_payload["responder"], "dry-run-user")
        self.assertEqual(result_payload["answers"]["Q1"], "do the thing")
        self.assertEqual(result_payload["discord"]["guild_id"], "dry-run")

    def test_dry_run_ignores_non_resume_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            status_dir = Path(tmp_dir) / "status"
            status_dir.mkdir()
            (status_dir / "escalated.json").write_text(
                json.dumps({"stage_key": "task_generator", "blocking_questions": []}),
                encoding="utf-8",
            )

            results: list[dict | None] = []

            def _writer() -> None:
                import time as _time

                _time.sleep(0.05)
                (status_dir / "discord_simulated_message.txt").write_text(
                    "hello world",
                    encoding="utf-8",
                )
                _time.sleep(0.15)
                (status_dir / "discord_simulated_message.txt").write_text(
                    "RESUME: all clear",
                    encoding="utf-8",
                )

            def _monitor() -> None:
                results.append(bridge_module.run_dry_run_loop(status_dir, []))

            t_write = threading.Thread(target=_writer, daemon=True)
            t_monitor = threading.Thread(target=_monitor, daemon=True)
            t_write.start()
            t_monitor.start()
            t_monitor.join(timeout=3)

        self.assertTrue(len(results) > 0)
        self.assertIsNotNone(results[0])


class WaitForResumeDiscordIntegrationTests(unittest.TestCase):
    def test_wait_for_resume_returns_none_when_no_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = make_config(Path(tmp_dir))
            result = discord_resume.wait_for_resume(config)
        self.assertIsNone(result)

    def test_wait_for_resume_reads_externally_written_resume_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = make_config(Path(tmp_dir))
            status_dir = Path(tmp_dir) / "WI-TEST" / "status"
            status_dir.mkdir(parents=True)
            escalated_path = status_dir / "escalated.json"
            escalated_path.write_text(
                json.dumps(
                    {
                        "stage_key": "task_generator",
                        "reason": "Test",
                        "blocking_questions": ["Q?"],
                    }
                ),
                encoding="utf-8",
            )

            def _write_resume_after_delay() -> None:
                import time as _time

                _time.sleep(0.1)
                (status_dir / "resume.json").write_text(
                    json.dumps(
                        {
                            "responder": "test-user",
                            "answers": {"Q1": "answer"},
                            "constraints": [],
                            "extra_context": "RESUME: Q1=answer",
                            "discord": {"guild_id": "dry-run"},
                        }
                    ),
                    encoding="utf-8",
                )

            t = threading.Thread(target=_write_resume_after_delay, daemon=True)
            with (
                mock.patch.object(
                    discord_resume,
                    "_start_discord_bridge",
                    return_value=None,
                ),
                mock.patch.object(discord_resume, "write_runner_log"),
            ):
                t.start()
                resolution = discord_resume.wait_for_resume(config, poll_seconds=1)

            self.assertIsNotNone(resolution)
            self.assertEqual(resolution["responder"], "test-user")
            self.assertEqual(resolution["answers"]["Q1"], "answer")
            self.assertFalse(escalated_path.exists())
            archive_dir = status_dir / "escalated_archive"
            archived = list(archive_dir.glob("*_escalated.json"))
            self.assertEqual(len(archived), 1)

    def test_start_discord_bridge_returns_none_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = make_config(Path(tmp_dir))
            status_dir = Path(tmp_dir) / "status"
            status_dir.mkdir(parents=True)
            escalated_path = status_dir / "escalated.json"
            escalated_path.write_text("{}", encoding="utf-8")

            import os as _os

            with mock.patch.dict(
                _os.environ,
                {"DISCORD_BOT_TOKEN": "", "DISCORD_DRY_RUN": ""},
                clear=False,
            ):
                env_copy = dict(_os.environ)
                env_copy.pop("DISCORD_BOT_TOKEN", None)
                env_copy.pop("DISCORD_DRY_RUN", None)
                with mock.patch.dict(_os.environ, env_copy, clear=True):
                    result = discord_resume._start_discord_bridge(
                        config,
                        escalated_path,
                        status_dir,
                    )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
