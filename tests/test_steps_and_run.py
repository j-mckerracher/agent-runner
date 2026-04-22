import os
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import run
from steps import build_intake_prompt
from workflow_inputs import DEFAULT_TEST_STORY_FILE


class IntakePromptTests(unittest.TestCase):
    def test_build_intake_prompt_for_ado_mentions_azure_devops(self):
        prompt = build_intake_prompt(
            intake_source="https://dev.azure.com/example/project/_workitems/edit/123",
            repo="/tmp/repo",
            change_id="WI-123",
            intake_mode="ado",
        )

        self.assertIn("Azure DevOps story link", prompt)
        self.assertIn("azure-devops-cli", prompt)
        self.assertIn("agent-context/WI-123/intake", prompt)

    def test_build_intake_prompt_for_synthetic_skips_required_ado_usage(self):
        prompt = build_intake_prompt(
            intake_source="/tmp/story.json",
            repo="/tmp/repo",
            change_id="TEST-AC-001",
            intake_mode="synthetic",
        )

        self.assertIn("synthetic test story", prompt)
        self.assertIn("Preserve the fixture contents under raw_input", prompt)
        self.assertIn("Do NOT require or use the azure-devops-cli skill", prompt)

    def test_build_intake_prompt_rejects_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported intake_mode"):
            build_intake_prompt(
                intake_source="/tmp/story.json",
                repo="/tmp/repo",
                change_id="TEST-AC-001",
                intake_mode="unsupported",
            )


class RunMainTests(unittest.TestCase):
    def test_main_defaults_to_bundled_synthetic_fixture(self):
        fake_parallel_future_1 = Mock()
        fake_parallel_future_2 = Mock()
        fake_parallel_future_1.result.return_value = None
        fake_parallel_future_2.result.return_value = None
        run_uow_loop = MagicMock()
        run_uow_loop.submit.side_effect = [fake_parallel_future_1, fake_parallel_future_2]

        with (
            patch.object(run, "use_runner_root") as use_runner_root,
            patch.object(run.steps, "step_intake", return_value="intake complete") as step_intake,
            patch.object(run, "run_eval_optimizer_loop") as eval_loop,
            patch.object(
                run,
                "load_assignments",
                return_value={
                    "execution_schedule": [
                        {
                            "batch": 1,
                            "parallel_execution": True,
                            "uows": [{"uow_id": "UOW-001"}, {"uow_id": "UOW-002"}],
                        },
                        {
                            "batch": 2,
                            "parallel_execution": False,
                            "uows": [{"uow_id": "UOW-003"}],
                        },
                    ]
                },
            ) as load_assignments,
            patch.object(run, "run_uow_eval_loop", run_uow_loop),
            patch.object(run.steps, "step_lessons_optimizer") as lessons_optimizer,
        ):
            result = run.main.fn(repo="/tmp/target-repo")

        use_runner_root.assert_called_once_with()
        step_intake.assert_called_once_with(
            intake_source=str(DEFAULT_TEST_STORY_FILE),
            repo="/tmp/target-repo",
            change_id="TEST-AC-001",
            intake_mode="synthetic",
        )
        self.assertEqual(eval_loop.call_count, 3)
        load_assignments.assert_called_once_with("TEST-AC-001")
        self.assertEqual(run_uow_loop.submit.call_count, 2)
        run_uow_loop.assert_called_once_with(
            uow_id="UOW-003",
            change_id="TEST-AC-001",
            repo="/tmp/target-repo",
        )
        lessons_optimizer.assert_called_once_with(change_id="TEST-AC-001", repo="/tmp/target-repo")
        self.assertEqual(result, str(DEFAULT_TEST_STORY_FILE))

    def test_main_uses_invocation_working_directory_when_repo_is_omitted(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_repo:
            fake_future = Mock()
            fake_future.result.return_value = None
            run_uow_loop = MagicMock()
            run_uow_loop.submit.return_value = fake_future

            try:
                os.chdir(temp_repo)
                with (
                    patch.object(run, "use_runner_root") as use_runner_root,
                    patch.object(run.steps, "step_intake", return_value="intake complete") as step_intake,
                    patch.object(run, "run_eval_optimizer_loop"),
                    patch.object(run, "load_assignments", return_value={"execution_schedule": []}),
                    patch.object(run, "run_uow_eval_loop", run_uow_loop),
                    patch.object(run.steps, "step_lessons_optimizer"),
                ):
                    run.main.fn()
            finally:
                os.chdir(original_cwd)

        use_runner_root.assert_called_once_with()
        step_kwargs = step_intake.call_args.kwargs
        self.assertEqual(step_kwargs["intake_source"], str(DEFAULT_TEST_STORY_FILE))
        self.assertEqual(os.path.realpath(step_kwargs["repo"]), os.path.realpath(temp_repo))
        self.assertEqual(step_kwargs["change_id"], "TEST-AC-001")
        self.assertEqual(step_kwargs["intake_mode"], "synthetic")


if __name__ == "__main__":
    unittest.main()



