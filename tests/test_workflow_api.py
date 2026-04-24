import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow_api


class _DummyTrace:
    def __init__(self):
        self.input = None
        self.output = None
        self.thread_id = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WorkflowContextTests(unittest.TestCase):
    def test_render_prompt_template_uses_relative_artifact_root(self):
        context = workflow_api.WorkflowContext(
            run_id="RUN-123",
            repo="/tmp/repo",
            runner="gemini",
            runner_model="gemini-2.5-pro",
        )

        prompt = workflow_api.render_prompt_template(
            "Review {artifact_root}/planning/tasks.yaml for {change_id} in {repo}.",
            context,
        )

        self.assertEqual(
            prompt,
            "Review agent-context/RUN-123/planning/tasks.yaml for RUN-123 in /tmp/repo.",
        )


class AgentStepFactoryTests(unittest.TestCase):
    def test_make_agent_step_renders_prompt_and_uses_context_runner(self):
        step = workflow_api.make_agent_step(
            agent_name="research-intake",
            trace_name="research-intake",
            prompt_template="Process {change_id} from {artifact_root}.",
        )
        context = workflow_api.WorkflowContext(
            run_id="RUN-123",
            repo="/tmp/repo",
            runner="gemini",
            runner_model="gemini-2.5-pro",
        )

        with (
            patch.object(workflow_api.opik, "start_as_current_trace", return_value=_DummyTrace()),
            patch.object(workflow_api, "run_agent_cmd", return_value="done") as run_agent_cmd,
        ):
            result = step.fn(workflow_context=context)

        self.assertEqual(result, "done")
        run_agent_cmd.assert_called_once_with(
            runner="gemini",
            prompt="Process RUN-123 from agent-context/RUN-123.",
            agent="research-intake",
            runner_model="gemini-2.5-pro",
        )

    def test_make_sdk_evaluator_step_records_pass_fail_feedback(self):
        step = workflow_api.make_sdk_evaluator_step(
            agent_name="research-evaluator",
            trace_name="research-evaluator",
            prompt_template="Evaluate {change_id}.",
        )
        context = workflow_api.WorkflowContext(
            run_id="RUN-456",
            repo="/tmp/repo",
            runner_model=None,
        )

        with (
            patch.object(workflow_api.opik, "start_as_current_trace", return_value=_DummyTrace()),
            patch.object(workflow_api, "call_evaluator_sdk", return_value="PASS") as call_evaluator_sdk,
            patch.object(workflow_api.opik.opik_context, "update_current_trace") as update_current_trace,
        ):
            result = step.fn(workflow_context=context)

        self.assertEqual(result, "PASS")
        call_evaluator_sdk.assert_called_once_with("Evaluate RUN-456.", "research-evaluator", model="claude-haiku-4-5-20251001")
        update_current_trace.assert_called_once()


class WorkflowArtifactLoaderTests(unittest.TestCase):
    def test_load_execution_plan_normalizes_single_dict_schedule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "agent-context" / "RUN-789"
            planning_dir = artifact_root / "planning"
            planning_dir.mkdir(parents=True)
            assignments_path = planning_dir / "assignments.json"
            assignments_path.write_text(
                json.dumps(
                    {
                        "execution_schedule": {"batch": 1, "uows": [{"uow_id": "UOW-001"}]},
                        "batch_2": {"batch": 2, "uows": [{"uow_id": "UOW-002"}]},
                    }
                ),
                encoding="utf-8",
            )
            context = workflow_api.WorkflowContext(
                run_id="RUN-789",
                repo="/tmp/repo",
                runner_model=None,
                artifact_root=artifact_root,
            )

            assignments = workflow_api.load_execution_plan(context)

        self.assertEqual(
            assignments["execution_schedule"],
            [
                {"batch": 1, "uows": [{"uow_id": "UOW-001"}]},
                {"batch": 2, "uows": [{"uow_id": "UOW-002"}]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
