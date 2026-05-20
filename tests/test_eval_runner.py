import subprocess
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from eval import runner as eval_runner


class EvalRunnerModelOverrideTests(unittest.TestCase):
    def test_inherits_compatible_env_model_for_same_runner(self):
        model = eval_runner.resolve_model_override(
            "claude",
            None,
            {"EVAL_MODEL": "claude-sonnet-4-6"},
        )

        self.assertEqual(model, "claude-sonnet-4-6")

    def test_drops_incompatible_env_model_when_runner_changes(self):
        model = eval_runner.resolve_model_override(
            "copilot",
            None,
            {"EVAL_MODEL": "claude-sonnet-4-6"},
        )

        self.assertIsNone(model)

    def test_copilot_alias_uses_copilot_model_choices(self):
        model = eval_runner.resolve_model_override(
            "copilot-enterprise",
            None,
            {"EVAL_MODEL": "gpt-5.4-mini"},
        )

        self.assertEqual(model, "gpt-5.4-mini")

    def test_explicit_model_is_preserved_for_downstream_validation(self):
        model = eval_runner.resolve_model_override(
            "copilot",
            "claude-sonnet-4-6",
            {"EVAL_MODEL": "gpt-5-mini"},
        )

        self.assertEqual(model, "claude-sonnet-4-6")

    def test_build_workflow_command_omits_model_when_no_override_is_resolved(self):
        args = Namespace(
            runner="copilot",
            model=None,
            log_level="warning",
            include_lessons=False,
            workflow_timeout=900,
        )
        command = eval_runner.build_workflow_command(Path("/tmp/bench"), Path("/tmp/workspace"), args)
        self.assertNotIn("--model", command)


class EvalRunnerProgressTests(unittest.TestCase):
    def test_workflow_env_adds_event_log_when_requested(self):
        env = eval_runner.workflow_env(Path("/tmp/events.jsonl"))

        self.assertEqual(env["AGENT_RUNNER_EVENT_LOG"], "/tmp/events.jsonl")
        self.assertEqual(env["AGENT_RUNNER_HEADLESS"], "1")

    def test_progress_tracks_stage_completion_without_lessons(self):
        progress = eval_runner.WorkflowProgress(stages=eval_runner.workflow_stage_names(include_lessons=False))

        changed = eval_runner.apply_workflow_event(progress, {"seq": 1, "type": "stage.start", "stage": "materialize"})
        self.assertTrue(changed)
        self.assertEqual(progress.percent(), 0)
        self.assertEqual(progress.render(), "Workflow progress: 0% — materialize (stage 1/6)")

        changed = eval_runner.apply_workflow_event(progress, {"seq": 2, "type": "stage.end", "stage": "materialize", "status": "ok"})
        self.assertTrue(changed)
        self.assertEqual(progress.percent(), 16)

        eval_runner.apply_workflow_event(progress, {"seq": 3, "type": "stage.start", "stage": "intake"})
        self.assertEqual(progress.render(), "Workflow progress: 16% — intake (stage 2/6)")

    def test_progress_tracks_execution_uow_completion(self):
        progress = eval_runner.WorkflowProgress(stages=eval_runner.workflow_stage_names(include_lessons=False))
        for seq, stage in enumerate(("materialize", "intake", "task-generation", "task-assignment"), start=1):
            eval_runner.apply_workflow_event(progress, {"seq": seq * 2 - 1, "type": "stage.start", "stage": stage})
            eval_runner.apply_workflow_event(progress, {"seq": seq * 2, "type": "stage.end", "stage": stage, "status": "ok"})

        eval_runner.apply_workflow_event(progress, {"seq": 9, "type": "stage.start", "stage": "execution"})
        eval_runner.apply_workflow_event(progress, {"seq": 10, "type": "workflow.plan", "total_uows": 4})
        self.assertEqual(progress.percent(), 66)
        self.assertEqual(progress.render(), "Workflow progress: 66% — execution (0/4 UoWs complete)")

        eval_runner.apply_workflow_event(progress, {"seq": 11, "type": "uow.end", "uow_id": "UOW-1", "status": "ok"})
        self.assertEqual(progress.percent(), 70)

        eval_runner.apply_workflow_event(progress, {"seq": 12, "type": "uow.end", "uow_id": "UOW-2", "status": "ok"})
        self.assertEqual(progress.percent(), 75)

        eval_runner.apply_workflow_event(progress, {"seq": 13, "type": "uow.end", "uow_id": "UOW-2", "status": "ok"})
        self.assertEqual(progress.percent(), 75)


if __name__ == "__main__":
    unittest.main()

