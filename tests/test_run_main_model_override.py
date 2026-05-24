from __future__ import annotations

import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run


class RunMainArgParseTests(unittest.TestCase):
    def test_easy__parse_args_normalizes_log_level(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo", "--log-level", "INFO"])
        self.assertEqual(args.log_level, "info")

    def test_easy__parse_args_rejects_invalid_log_level(self) -> None:
        with self.assertRaises(SystemExit):
            run.parse_args(["--repo", "/tmp/repo", "--log-level", "verbose"])

    def test_easy__parse_args_accepts_skip_materialize(self) -> None:
        args = run.parse_args(["--repo", "/tmp/repo", "--skip-materialize"])
        self.assertTrue(args.skip_materialize)

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

    def test_easy__main_configures_requested_log_level(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-LOG-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch description",
        )

        with patch.object(run, "configure_logging") as configure_logging_mock, \
             patch.object(run, "resolve_workflow_input", return_value=workflow_input), \
             patch.object(run, "use_runner_root"), \
             patch.object(run, "clean_workspace"), \
             patch.object(run, "_load_runner_config", return_value=self._config()), \
             patch.object(run, "_emit"), \
             patch.object(run, "_write_workflow_status"), \
             patch.object(run, "_require_file"), \
             patch.object(run, "_require_dir"), \
             patch("core.opik_tracing.opik.configure"), \
             patch("core.opik_tracing.opik.Opik", return_value=Mock()), \
             patch("signal.signal"), \
             patch("core.materialize.run_materialization"), \
             patch("core.steps.step_intake"), \
             patch("core.evaluator_optimizer_loops.run_eval_optimizer_loop"), \
             patch("core.evaluator_optimizer_loops.run_uow_eval_loop"), \
             patch("run.load_assignments", return_value={"batches": []}), \
             patch("core.steps.step_lessons_optimizer"):
            run.main(
                repo="/tmp/repo",
                story_file="/tmp/story.json",
                runner="copilot",
                log_level="debug",
                skip_materialize=True,
            )

        configure_logging_mock.assert_called_once_with("debug")

    def test_medium__explicit_model_flows_through_runner_model_kwargs(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        intake_mock = Mock()
        with patch.object(run, "resolve_workflow_input", return_value=workflow_input), \
             patch.object(run, "use_runner_root"), \
             patch.object(run, "clean_workspace"), \
             patch.object(run, "_load_runner_config", return_value=self._config()), \
             patch.object(run, "_emit"), \
             patch.object(run, "_write_workflow_status"), \
             patch.object(run, "_require_file"), \
             patch.object(run, "_require_dir"), \
             patch("core.opik_tracing.opik.configure"), \
             patch("core.opik_tracing.opik.Opik", return_value=Mock()), \
             patch("signal.signal"), \
             patch("core.materialize.run_materialization"), \
             patch("core.steps.step_intake", intake_mock), \
             patch("core.evaluator_optimizer_loops.run_eval_optimizer_loop"), \
             patch("core.evaluator_optimizer_loops.run_uow_eval_loop"), \
             patch("run.load_assignments", return_value={"batches": []}), \
             patch("core.steps.step_lessons_optimizer"):
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

    def test_medium__server_driven_runs_skip_duplicate_workspace_cleanup(self) -> None:
        workflow_input = SimpleNamespace(
            repo="/tmp/repo",
            change_id="TEST-EVENT-001",
            intake_mode="synthetic",
            intake_source="/tmp/story.json",
            branch_description_source="Test branch",
        )

        with patch.dict(run.os.environ, {"AGENT_RUNNER_EVENT_LOG": "/tmp/events.jsonl"}, clear=False), \
             patch.object(run, "resolve_workflow_input", return_value=workflow_input), \
             patch.object(run, "use_runner_root"), \
             patch.object(run, "clean_workspace") as clean_workspace_mock, \
             patch.object(run, "_load_runner_config", return_value=self._config()), \
             patch.object(run, "_emit"), \
             patch.object(run, "_write_workflow_status"), \
             patch.object(run, "_require_file"), \
             patch.object(run, "_require_dir"), \
             patch("core.opik_tracing.opik.configure"), \
             patch("core.opik_tracing.opik.Opik", return_value=Mock()), \
             patch("signal.signal"), \
             patch("core.materialize.run_materialization"), \
             patch("core.steps.step_intake"), \
             patch("core.evaluator_optimizer_loops.run_eval_optimizer_loop"), \
             patch("core.evaluator_optimizer_loops.run_uow_eval_loop"), \
             patch("run.load_assignments", return_value={"batches": []}), \
             patch("core.steps.step_lessons_optimizer"):
            run.main(
                repo="/tmp/repo",
                story_file="/tmp/story.json",
                runner="copilot",
                model="gpt-5-mini",
                skip_materialize=True,
            )

        clean_workspace_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
