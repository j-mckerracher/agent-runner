from __future__ import annotations

import logging
import json
import sys
import tempfile
import types
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import core
from core.cli_logging import DEFAULT_LOG_FORMAT, DemoteHttpxHealthcheckFilter, LocalTimezoneFormatter
import run


class RunMainArgParseTests(unittest.TestCase):
    def test_easy__parse_args_normalizes_log_level(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo", "--log-level", "INFO"])
        self.assertEqual(args.log_level, "info")

    def test_easy__parse_args_rejects_invalid_log_level(self) -> None:
        with self.assertRaises(SystemExit):
            run.parse_args(["--repo", "/tmp/repo", "--log-level", "verbose"])

    def test_easy__parse_args_defaults_to_skip_materialize(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo"])
        self.assertTrue(args.skip_materialize)

    def test_easy__parse_args_accepts_skip_materialize(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo", "--skip-materialize"])
        self.assertTrue(args.skip_materialize)

    def test_easy__parse_args_accepts_explicit_materialize(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo", "--materialize"])
        self.assertFalse(args.skip_materialize)

    def test_easy__parse_args_accepts_repeated_agent_overrides(self) -> None:
        args = run.parse_args([
            "--repo", "/tmp/repo",
            "--agent-runner", "qa-engineer=codex",
            "--agent-model", "qa-engineer=gpt-5.5",
            "--agent-model", "qa-evaluator=deepseek-v4-pro:cloud",
        ])

        self.assertEqual(
            args.agent_llm_overrides,
            {
                "qa-engineer": {"runner": "codex", "model": "gpt-5.5"},
                "qa-evaluator": {"model": "deepseek-v4-pro:cloud"},
            },
        )

    def test_easy__parse_args_rejects_invalid_agent_override_form(self) -> None:
        with self.assertRaises(SystemExit):
            run.parse_args(["--repo", "/tmp/repo", "--agent-runner", "qa-engineer"])

    def test_easy__story_source_metadata_counts_original_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            story_path = Path(td) / "story.json"
            story_path.write_text(
                json.dumps({
                    "title": "Story",
                    "description": "Desc",
                    "acceptance_criteria": {"AC1": "One", "AC2": "Two"},
                }),
                encoding="utf-8",
            )

            metadata = run._story_source_metadata(intake_mode="synthetic", intake_source=str(story_path))

        self.assertEqual(metadata["source"], "story_file")
        self.assertEqual(metadata["story_file"], str(story_path))
        self.assertEqual(metadata["original_ac_count"], 2)

    def test_easy__manual_story_source_metadata_counts_original_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            story_path = Path(td) / "manual_story.json"
            story_path.write_text(
                json.dumps({
                    "title": "Story",
                    "description": "Desc",
                    "acceptance_criteria": "- One\n- Two",
                }),
                encoding="utf-8",
            )

            metadata = run._story_source_metadata(intake_mode="manual", intake_source=str(story_path))

        self.assertEqual(metadata["source"], "manual")
        self.assertEqual(metadata["manual_story_file"], str(story_path))
        self.assertEqual(metadata["original_ac_count"], 2)

    def test_easy__workflow_stage_names_use_central_stage_constants(self) -> None:
        self.assertEqual(run._workflow_stage_names(), list(run.WORKFLOW_STAGES))
        self.assertEqual(run.WORKFLOW_STAGES[0], run.STAGE_MATERIALIZE)
        self.assertEqual(run.WORKFLOW_STAGES[-1], run.STAGE_PR_REVIEW)

    def test_easy__event_log_and_summary_refs_use_central_artifact_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context_root = Path(tmpdir) / "agent-context"
            logs_root = Path(tmpdir) / "logs"
            with patch.object(run, "AGENT_CONTEXT_ROOT", context_root), patch.object(run, "LOGS_ROOT", logs_root):
                event_path = run._event_log_path("TEST-PATH-001")
                status_path = run._workflow_status_path("TEST-PATH-001")
                summary_event_ref = run._summary_artifact_ref(run.ARTIFACT_FILE_EVENTS)

        self.assertEqual(
            event_path,
            logs_root / "TEST-PATH-001" / run.ARTIFACT_FILE_EVENTS,
        )
        self.assertEqual(
            status_path,
            context_root / "TEST-PATH-001" / run.ARTIFACT_DIR_SUMMARY / run.WORKFLOW_STATUS_FILENAME,
        )
        self.assertEqual(
            summary_event_ref,
            f"{run.ARTIFACT_DIR_SUMMARY}/{run.ARTIFACT_FILE_EVENTS}",
        )

    def test_medium__stage_prompt_builders_use_central_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            context_root = Path(tmpdir) / "agent-context"
            with patch.object(run, "AGENT_CONTEXT_ROOT", context_root):
                task_prompt = run._task_generation_input(change_id="TEST-PROMPT-001", repo="/tmp/repo")
                assigner_prompt = run._assignment_input(change_id="TEST-PROMPT-001", repo="/tmp/repo")
                qa_prompt = run._qa_producer_input(
                    change_id="TEST-PROMPT-001",
                    repo="/tmp/repo",
                    evidence_root=run._qa_evidence_root("TEST-PROMPT-001"),
                )

        base = context_root / "TEST-PROMPT-001"
        self.assertIn(str(base / run.ARTIFACT_DIR_INTAKE), task_prompt)
        self.assertIn(str(base / run.ARTIFACT_DIR_PLANNING / run.ARTIFACT_FILE_TASKS), assigner_prompt)
        self.assertIn(str(base / run.ARTIFACT_DIR_INTAKE / run.ARTIFACT_FILE_STORY), assigner_prompt)
        self.assertIn(str(base / run.ARTIFACT_DIR_QA / run.ARTIFACT_FILE_QA_REPORT), qa_prompt)
        self.assertIn(run.ARTIFACT_FILE_QA_REPORT, qa_prompt)


class RunAgentLlmOverrideResolverTests(unittest.TestCase):
    def test_easy__no_overrides_uses_global_runner_and_model(self) -> None:
        resolved = run.resolve_agent_llm_overrides(
            runner="copilot",
            model="gpt-5.4",
            config={},
        )

        self.assertEqual(resolved["qa-engineer"], {"runner": "copilot", "model": "gpt-5.4"})
        self.assertEqual(resolved["pr-reviewer"], {"runner": "copilot", "model": "gpt-5.4"})

    def test_easy__global_model_still_wins_over_configured_agent_default(self) -> None:
        resolved = run.resolve_agent_llm_overrides(
            runner="copilot",
            model="gpt-5.4",
            config={
                "agent_model_defaults": {
                    "qa-engineer": {"copilot": "gpt-5-mini"},
                }
            },
        )

        self.assertEqual(resolved["qa-engineer"], {"runner": "copilot", "model": "gpt-5.4"})

    def test_easy__runner_only_override_uses_runner_default_model(self) -> None:
        resolved = run.resolve_agent_llm_overrides(
            runner="copilot",
            model="gpt-5.4",
            config={},
            agent_llm_overrides={"qa-engineer": {"runner": "codex"}},
        )

        self.assertEqual(resolved["qa-engineer"], {"runner": "codex", "model": "gpt-5.5"})
        self.assertEqual(resolved["qa-evaluator"], {"runner": "copilot", "model": "gpt-5.4"})

    def test_easy__runner_and_model_override_validates_closed_runner(self) -> None:
        resolved = run.resolve_agent_llm_overrides(
            runner="copilot",
            model="gpt-5.4",
            config={},
            agent_llm_overrides={"intake": {"runner": "claude", "model": "claude-sonnet-4-6"}},
        )

        self.assertEqual(resolved["intake"], {"runner": "claude", "model": "claude-sonnet-4-6"})

    def test_easy__invalid_agent_runner_and_model_raise_clear_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown agent override"):
            run.resolve_agent_llm_overrides(
                runner="copilot",
                model="gpt-5.4",
                config={},
                agent_llm_overrides={"not-an-agent": {"runner": "codex"}},
            )

        with self.assertRaisesRegex(ValueError, "Unknown runner"):
            run.resolve_agent_llm_overrides(
                runner="copilot",
                model="gpt-5.4",
                config={},
                agent_llm_overrides={"qa-engineer": {"runner": "bogus"}},
            )

        with self.assertRaisesRegex(ValueError, "not valid for runner 'claude'"):
            run.resolve_agent_llm_overrides(
                runner="copilot",
                model="gpt-5.4",
                config={},
                agent_llm_overrides={"qa-engineer": {"runner": "claude", "model": "bogus"}},
            )


class RunMainStagePlumbingTests(unittest.TestCase):
    def _config(self, **overrides):
        config = {
            "opik": {
                "dashboard_url": "http://localhost:5173",
                "workspace_name": "default",
                "project_id": "project-123",
                "project_name": "agent-workbench",
            }
        }
        config.update(overrides)
        return config

    def _install_fake_workflow_modules(self, stack: ExitStack):
        steps_module = types.ModuleType("core.steps")
        steps_module.step_intake = Mock()
        steps_module.step_pr_review = Mock(return_value="/tmp/pr_review.md")
        steps_module.step_lessons_optimizer = Mock()
        steps_module.step_task_gen_producer = Mock()
        steps_module.step_task_gen_evaluator = Mock()
        steps_module.step_task_assigner = Mock()
        steps_module.step_assignment_evaluator = Mock()
        steps_module.step_qa_engineer = Mock()
        steps_module.step_qa_evaluator = Mock()

        loops_module = types.ModuleType("core.evaluator_optimizer_loops")
        loops_module.run_eval_optimizer_loop = Mock()
        loops_module.run_uow_eval_loop = Mock()

        stack.enter_context(
            patch.dict(
                sys.modules,
                {
                    "core.steps": steps_module,
                    "core.evaluator_optimizer_loops": loops_module,
                },
            )
        )
        stack.enter_context(patch.object(core, "steps", steps_module, create=True))
        stack.enter_context(patch.object(core, "evaluator_optimizer_loops", loops_module, create=True))
        return steps_module, loops_module

    def test_easy__main_configures_requested_log_level(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-LOG-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch description",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                configure_logging_mock = stack.enter_context(patch.object(run, "configure_logging"))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    log_level="debug",
                    skip_materialize=True,
                )

        configure_logging_mock.assert_called_once_with("debug")

    def test_easy__main_prepares_repo_branch_before_intake(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-BRANCH-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Fix flaky invoice export",
        )
        call_order: list[str] = []
        class StopAfterIntake(RuntimeError):
            pass

        def fake_prepare_repo_branch(**kwargs):
            call_order.append("branch")
            return "feature/test-branch"

        def fake_step_intake(**kwargs):
            call_order.append("intake")
            raise StopAfterIntake("stop after intake")

        with ExitStack() as stack:
            steps_module, _loops_module = self._install_fake_workflow_modules(stack)
            steps_module.step_intake.side_effect = fake_step_intake
            stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
            stack.enter_context(patch.object(run, "use_runner_root"))
            stack.enter_context(patch.object(run, "clean_workspace"))
            stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
            stack.enter_context(patch.object(run, "_emit"))
            stack.enter_context(patch.object(run, "_write_workflow_status"))
            stack.enter_context(patch.object(run, "_require_file"))
            stack.enter_context(patch.object(run, "_require_dir"))
            prepare_repo_branch_mock = stack.enter_context(
                patch.object(run, "prepare_repo_branch", side_effect=fake_prepare_repo_branch)
            )
            stack.enter_context(patch("core.opik_tracing.opik.configure"))
            stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
            stack.enter_context(patch("signal.signal"))
            stack.enter_context(patch("core.materialize.run_materialization"))
            stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
            with self.assertRaises(StopAfterIntake):
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5.4",
                    skip_materialize=True,
                )

        prepare_repo_branch_mock.assert_called_once_with(
            repo="/tmp/repo",
            change_id="TEST-BRANCH-001",
            description_source="Fix flaky invoice export",
        )
        steps_module.step_intake.assert_called_once()
        self.assertEqual(call_order[:2], ["branch", "intake"])
        self.assertEqual(steps_module.step_intake.call_args.kwargs["feature_branch"], "feature/test-branch")

    def test_easy__configure_logging_uses_local_timezone_formatter(self) -> None:
        httpx_logger = logging.getLogger("httpx")
        original_filters = list(httpx_logger.filters)
        httpx_logger.filters = [
            log_filter
            for log_filter in httpx_logger.filters
            if not isinstance(log_filter, DemoteHttpxHealthcheckFilter)
        ]
        with patch.dict(run.os.environ, {}, clear=True), patch.object(run.logging, "basicConfig") as basic_config_mock:
            try:
                run.configure_logging("info")

                kwargs = basic_config_mock.call_args.kwargs
                self.assertEqual(kwargs["level"], logging.INFO)
                self.assertTrue(kwargs["force"])
                self.assertEqual(len(kwargs["handlers"]), 1)
                handler = kwargs["handlers"][0]
                self.assertEqual(handler.level, logging.INFO)
                self.assertIsInstance(handler.formatter, LocalTimezoneFormatter)
                self.assertEqual(handler.formatter._style._fmt, DEFAULT_LOG_FORMAT)
                self.assertTrue(
                    any(
                        isinstance(log_filter, DemoteHttpxHealthcheckFilter)
                        for log_filter in httpx_logger.filters
                    )
                )
            finally:
                httpx_logger.filters = original_filters

    def test_medium__event_log_handler_captures_only_selected_python_log_levels(self) -> None:
        from server.events import read_all

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
            event_log_path = fh.name
        try:
            Path(event_log_path).write_text("", encoding="utf-8")
            with patch.dict(run.os.environ, {"AGENT_RUNNER_EVENT_LOG": event_log_path}, clear=False):
                run.configure_logging("warning")
                test_logger = logging.getLogger("tests.python_log_level")
                test_logger.info("hidden info")
                test_logger.warning("shown warning")

            events = [
                event for event in read_all(event_log_path)
                if event.get("logger") == "tests.python_log_level"
            ]

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["source"], "python_logging")
            self.assertEqual(events[0]["level"], "warning")
            self.assertEqual(events[0]["msg"], "shown warning")
        finally:
            try:
                Path(event_log_path).unlink()
            except OSError:
                pass

    def test_medium__explicit_model_flows_through_runner_model_kwargs(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        intake_mock = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                steps_module, _loops_module = self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                steps_module.step_intake = intake_mock
                stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5.4",
                    skip_materialize=True,
                )

        intake_mock.assert_called_once()
        kwargs = intake_mock.call_args.kwargs
        self.assertEqual(kwargs["runner"], "copilot")
        self.assertEqual(kwargs["runner_model"], "gpt-5.4")
        self.assertEqual(kwargs["feature_branch"], "feature/test-branch")

    def test_medium__agent_overrides_flow_to_stage_and_loop_calls(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-OVERRIDE-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )
        loop_calls: list[dict] = []
        uow_calls: list[dict] = []

        def fake_eval_loop(*_args, **kwargs):
            loop_calls.append(kwargs)

        def fake_uow_loop(**kwargs):
            uow_calls.append(kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                steps_module, loops_module = self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                intake_mock = steps_module.step_intake
                loops_module.run_eval_optimizer_loop.side_effect = fake_eval_loop
                loops_module.run_uow_eval_loop.side_effect = fake_uow_loop
                pr_review_mock = steps_module.step_pr_review
                stack.enter_context(patch("run.load_assignments", return_value={"batches": [{"batch_id": 1, "parallel_execution": False, "uows": [{"uow_id": "UOW-001"}]}]}))
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5.4",
                    skip_materialize=True,
                    agent_llm_overrides={
                        "intake": {"runner": "claude", "model": "claude-sonnet-4-6"},
                        "task-generator": {"runner": "codex", "model": "gpt-5.5"},
                        "task-plan-evaluator": {"runner": "openai-compat", "model": "judge:model"},
                        "software-engineer": {"runner": "codex", "model": "gpt-5.4"},
                        "implementation-evaluator": {"runner": "claude", "model": "claude-haiku-4-5-20251001"},
                        "qa-engineer": {"runner": "gemini", "model": "gemini-2.5-flash"},
                        "qa-evaluator": {"runner": "openai-compat", "model": "qa:judge"},
                        "pr-reviewer": {"runner": "codex", "model": "gpt-5.2"},
                    },
                )

        self.assertEqual(intake_mock.call_args.kwargs["runner"], "claude")
        self.assertEqual(intake_mock.call_args.kwargs["runner_model"], "claude-sonnet-4-6")
        self.assertEqual(loop_calls[0]["runner"], "codex")
        self.assertEqual(loop_calls[0]["runner_model"], "gpt-5.5")
        self.assertEqual(loop_calls[0]["evaluator_runner"], "openai-compat")
        self.assertEqual(loop_calls[0]["evaluator_runner_model"], "judge:model")
        self.assertEqual(uow_calls[0]["runner"], "codex")
        self.assertEqual(uow_calls[0]["evaluator_runner"], "claude")
        self.assertEqual(loop_calls[-1]["runner"], "gemini")
        self.assertEqual(loop_calls[-1]["evaluator_runner_model"], "qa:judge")
        self.assertEqual(pr_review_mock.call_args.kwargs["runner"], "codex")
        self.assertEqual(pr_review_mock.call_args.kwargs["runner_model"], "gpt-5.2")

    def test_medium__server_driven_runs_skip_duplicate_workspace_cleanup(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-EVENT-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                stack.enter_context(patch.dict(run.os.environ, {"AGENT_RUNNER_EVENT_LOG": "/tmp/events.jsonl"}, clear=False))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                clean_workspace_mock = stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5-mini",
                    skip_materialize=True,
                )

        clean_workspace_mock.assert_not_called()

    def test_medium__main_runs_pr_review_after_qa_as_terminal_stage(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-PR-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        stage_order: list[str] = []

        def fake_eval_loop(producer_func, producer_input, evaluator_func, evaluator_prompt, **kwargs):  # noqa: ARG001
            if "Perform QA validation" in producer_input:
                stage_order.append("qa")

        def fake_pr_review(**kwargs):  # noqa: ARG001
            stage_order.append("pr-review")
            return "/tmp/pr_review.md"

        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                steps_module, loops_module = self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                write_status_mock = stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                loops_module.run_eval_optimizer_loop.side_effect = fake_eval_loop
                pr_review_mock = steps_module.step_pr_review
                pr_review_mock.side_effect = fake_pr_review
                stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
                lessons_mock = steps_module.step_lessons_optimizer
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5-mini",
                    skip_materialize=True,
                )

        self.assertEqual(stage_order, ["qa", "pr-review"])
        pr_review_mock.assert_called_once()
        lessons_mock.assert_not_called()
        self.assertEqual(write_status_mock.call_args.kwargs["last_completed_stage"], "pr-review")

    def test_medium__main_skips_pr_review_for_evaluation_runs(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-EVAL-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        stage_order: list[str] = []

        def fake_eval_loop(producer_func, producer_input, evaluator_func, evaluator_prompt, **kwargs):  # noqa: ARG001
            if "Perform QA validation" in producer_input:
                stage_order.append("qa")

        with tempfile.TemporaryDirectory() as tmpdir:
            with ExitStack() as stack:
                steps_module, loops_module = self._install_fake_workflow_modules(stack)
                stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
                stack.enter_context(patch.dict(run.os.environ, {"AGENT_RUNNER_EVALUATION_RUN": "1"}, clear=False))
                stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
                stack.enter_context(patch.object(run, "use_runner_root"))
                stack.enter_context(patch.object(run, "clean_workspace"))
                stack.enter_context(patch.object(run, "_load_runner_config", return_value=self._config()))
                stack.enter_context(patch.object(run, "_emit"))
                write_status_mock = stack.enter_context(patch.object(run, "_write_workflow_status"))
                stack.enter_context(patch.object(run, "_require_file"))
                stack.enter_context(patch.object(run, "_require_dir"))
                stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/test-branch"))
                stack.enter_context(patch("core.opik_tracing.opik.configure"))
                stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
                stack.enter_context(patch("signal.signal"))
                stack.enter_context(patch("core.materialize.run_materialization"))
                loops_module.run_eval_optimizer_loop.side_effect = fake_eval_loop
                pr_review_mock = steps_module.step_pr_review
                stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))
                run.main(
                    repo="/tmp/repo",
                    story_file="/tmp/story.json",
                    runner="copilot",
                    model="gpt-5-mini",
                    skip_materialize=True,
                )

        self.assertEqual(stage_order, ["qa"])
        pr_review_mock.assert_not_called()
        self.assertEqual(write_status_mock.call_args.kwargs["last_completed_stage"], "qa")


if __name__ == "__main__":
    unittest.main()
