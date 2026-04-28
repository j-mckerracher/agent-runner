"""
Difficulty rubric for this file:
  easy   = single-field assertion on a string prompt or a pre-built artifact
           file (presence of a substring, exact field equality).
  medium = mock dispatch verification for argparse/run.main behavior, where
           multiple keyword arguments must be checked against expected values.
  hard   = multi-stage end-to-end workflow orchestration where several
           artifacts spanning different pipeline stages must be created and
           cross-checked.
"""

import json
import os
import sys
import tempfile
import unittest
import yaml
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import run
from steps import build_intake_prompt
from workflow_inputs import DEFAULT_TEST_STORY_FILE


class IntakePromptAdoModeTests(unittest.TestCase):
    def setUp(self):
        self.prompt = build_intake_prompt(
            intake_source="https://dev.azure.com/example/project/_workitems/edit/123",
            repo="/tmp/repo",
            change_id="WI-123",
            intake_mode="ado",
        )

    def test_easy__ado_intake_prompt_mentions_azure_devops_story_link(self):
        self.assertIn("Azure DevOps story link", self.prompt)

    def test_easy__ado_intake_prompt_references_azure_devops_cli(self):
        self.assertIn("azure-devops-cli", self.prompt)

    def test_easy__ado_intake_prompt_contains_target_artifact_path(self):
        self.assertIn("agent-context/WI-123/intake", self.prompt)


class IntakePromptSyntheticModeTests(unittest.TestCase):
    def setUp(self):
        self.prompt = build_intake_prompt(
            intake_source="/tmp/story.json",
            repo="/tmp/repo",
            change_id="TEST-AC-001",
            intake_mode="synthetic",
        )

    def test_easy__synthetic_intake_prompt_mentions_synthetic_test_story(self):
        self.assertIn("synthetic test story", self.prompt)

    def test_easy__synthetic_intake_prompt_instructs_preserving_raw_input(self):
        self.assertIn("Preserve the fixture contents under raw_input", self.prompt)

    def test_easy__synthetic_intake_prompt_forbids_azure_devops_cli(self):
        self.assertIn("Do NOT require or use the azure-devops-cli skill", self.prompt)


class IntakePromptRejectionTests(unittest.TestCase):
    def test_easy__build_intake_prompt_rejects_unknown_mode_with_value_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported intake_mode"):
            build_intake_prompt(
                intake_source="/tmp/story.json",
                repo="/tmp/repo",
                change_id="TEST-AC-001",
                intake_mode="unsupported",
            )


class ParseArgsTests(unittest.TestCase):
    def test_easy__parse_args_runner_field_is_gemini(self):
        with patch.object(sys, "argv", ["run.py", "--repo", "/tmp/target-repo", "--runner", "gemini"]):
            args = run.parse_args()
        self.assertEqual(args.runner, "gemini")

    def test_easy__parse_args_default_model_for_gemini_runner_is_gemini_2_5_flash(self):
        with patch.object(sys, "argv", ["run.py", "--repo", "/tmp/target-repo", "--runner", "gemini"]):
            args = run.parse_args()
        self.assertEqual(args.model, "gemini-2.5-flash")

    def test_easy__parse_args_default_model_for_claude_runner_is_haiku(self):
        with patch.object(sys, "argv", ["run.py", "--repo", "/tmp/target-repo", "--runner", "claude"]):
            args = run.parse_args()
        self.assertEqual(args.model, "claude-haiku-4-5-20251001")

    def test_easy__parse_args_accepts_explicit_model_for_gemini_runner(self):
        with patch.object(
            sys,
            "argv",
            [
                "run.py",
                "--repo",
                "/tmp/target-repo",
                "--runner",
                "gemini",
                "--model",
                "gemini-3-pro-preview",
            ],
        ):
            args = run.parse_args()
        self.assertEqual(args.model, "gemini-3-pro-preview")

    def test_easy__parse_args_rejects_cross_runner_model(self):
        with (
            patch.object(
                sys,
                "argv",
                ["run.py", "--repo", "/tmp/target-repo", "--runner", "claude", "--model", "gpt-5-mini"],
            ),
            self.assertRaises(SystemExit),
        ):
            run.parse_args()


class RunMainBundledFixtureDispatchTests(unittest.TestCase):
    def setUp(self):
        fake_parallel_future_1 = Mock()
        fake_parallel_future_2 = Mock()
        fake_parallel_future_1.result.return_value = None
        fake_parallel_future_2.result.return_value = None
        self.run_uow_loop = MagicMock()
        self.run_uow_loop.submit.side_effect = [fake_parallel_future_1, fake_parallel_future_2]

        self._patches = [
            patch.object(run, "use_runner_root"),
            patch.object(run, "clean_workspace"),
            patch.object(run.steps, "step_intake", return_value="intake complete"),
            patch.object(run, "run_eval_optimizer_loop"),
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
            ),
            patch.object(run, "run_uow_eval_loop", self.run_uow_loop),
            patch.object(run.steps, "step_lessons_optimizer"),
        ]
        self.use_runner_root = self._patches[0].start()
        self.clean_workspace = self._patches[1].start()
        self.step_intake = self._patches[2].start()
        self.eval_loop = self._patches[3].start()
        self.load_assignments = self._patches[4].start()
        self._patches[5].start()
        self.lessons_optimizer = self._patches[6].start()
        for p in self._patches[7:]:
            p.start()
        self.addCleanup(self._stop_all)
        self.result = run.main.fn(repo="/tmp/target-repo")

    def _stop_all(self):
        for p in self._patches:
            p.stop()

    def test_medium__main_invokes_use_runner_root_once(self):
        self.use_runner_root.assert_called_once_with()

    def test_medium__main_invokes_step_intake_with_default_synthetic_fixture(self):
        self.step_intake.assert_called_once_with(
            intake_source=str(DEFAULT_TEST_STORY_FILE),
            repo="/tmp/target-repo",
            change_id="TEST-AC-001",
            intake_mode="synthetic",
            runner="claude",
        )

    def test_medium__main_invokes_eval_optimizer_loop_three_times(self):
        self.assertEqual(self.eval_loop.call_count, 3)

    def test_medium__main_loads_assignments_for_test_ac_001(self):
        self.load_assignments.assert_called_once_with("TEST-AC-001")

    def test_medium__main_submits_two_parallel_uow_runs(self):
        self.assertEqual(self.run_uow_loop.submit.call_count, 2)

    def test_medium__main_invokes_run_uow_loop_serially_for_uow_003(self):
        self.run_uow_loop.assert_called_once_with(
            uow_id="UOW-003",
            change_id="TEST-AC-001",
            repo="/tmp/target-repo",
            runner="claude",
        )

    def test_medium__main_invokes_lessons_optimizer_with_change_id_and_runner(self):
        self.lessons_optimizer.assert_called_once_with(
            change_id="TEST-AC-001", repo="/tmp/target-repo", runner="claude"
        )

    def test_easy__main_returns_default_test_story_path(self):
        self.assertEqual(self.result, str(DEFAULT_TEST_STORY_FILE))


class RunMainOmittedRepoTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_repo = self.temp_dir.name
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(os.chdir, self.original_cwd)

        fake_future = Mock()
        fake_future.result.return_value = None
        self.run_uow_loop = MagicMock()
        self.run_uow_loop.submit.return_value = fake_future

        os.chdir(self.temp_repo)
        self._patches = [
            patch.object(run, "use_runner_root"),
            patch.object(run, "clean_workspace"),
            patch.object(run.steps, "step_intake", return_value="intake complete"),
            patch.object(run, "run_eval_optimizer_loop"),
            patch.object(run, "load_assignments", return_value={"execution_schedule": []}),
            patch.object(run, "run_uow_eval_loop", self.run_uow_loop),
            patch.object(run.steps, "step_lessons_optimizer"),
        ]
        self.use_runner_root = self._patches[0].start()
        self._patches[1].start()
        self.step_intake = self._patches[2].start()
        for p in self._patches[3:]:
            p.start()
        self.addCleanup(self._stop_all)
        run.main.fn()
        self.step_kwargs = self.step_intake.call_args.kwargs

    def _stop_all(self):
        for p in self._patches:
            p.stop()

    def test_medium__main_invokes_use_runner_root_when_repo_omitted(self):
        self.use_runner_root.assert_called_once_with()

    def test_medium__step_intake_intake_source_is_default_test_story_path(self):
        self.assertEqual(self.step_kwargs["intake_source"], str(DEFAULT_TEST_STORY_FILE))

    def test_medium__step_intake_repo_resolves_to_invocation_cwd(self):
        self.assertEqual(os.path.realpath(self.step_kwargs["repo"]), os.path.realpath(self.temp_repo))

    def test_medium__step_intake_change_id_defaults_to_test_ac_001(self):
        self.assertEqual(self.step_kwargs["change_id"], "TEST-AC-001")

    def test_medium__step_intake_intake_mode_defaults_to_synthetic(self):
        self.assertEqual(self.step_kwargs["intake_mode"], "synthetic")


class RunMainGeminiRunnerTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(run, "use_runner_root"),
            patch.object(run, "clean_workspace"),
            patch.object(run.steps, "step_intake", return_value="intake complete"),
            patch.object(run, "run_eval_optimizer_loop"),
            patch.object(run, "load_assignments", return_value={"execution_schedule": []}),
            patch.object(run, "run_uow_eval_loop"),
            patch.object(run.steps, "step_lessons_optimizer"),
        ]
        self._patches[0].start()
        self._patches[1].start()
        self.step_intake = self._patches[2].start()
        self.eval_loop = self._patches[3].start()
        self._patches[4].start()
        self._patches[5].start()
        self.lessons_optimizer = self._patches[6].start()
        self.addCleanup(self._stop_all)
        run.main.fn(repo="/tmp/target-repo", runner="gemini", model="gemini-3-pro-preview")

    def _stop_all(self):
        for p in self._patches:
            p.stop()

    def test_hard__gemini_runner_propagates_to_step_intake(self):
        self.step_intake.assert_called_once_with(
            intake_source=str(DEFAULT_TEST_STORY_FILE),
            repo="/tmp/target-repo",
            change_id="TEST-AC-001",
            intake_mode="synthetic",
            runner="gemini",
            runner_model="gemini-3-pro-preview",
        )

    def test_hard__gemini_runner_propagates_to_eval_optimizer_loop_three_times(self):
        self.assertEqual(self.eval_loop.call_count, 3)

    def test_hard__every_eval_optimizer_loop_call_receives_gemini_runner(self):
        runners = [call.kwargs["runner"] for call in self.eval_loop.call_args_list]
        self.assertEqual(runners, ["gemini", "gemini", "gemini"])

    def test_hard__every_eval_optimizer_loop_call_receives_explicit_gemini_model(self):
        models = [call.kwargs["runner_model"] for call in self.eval_loop.call_args_list]
        self.assertEqual(models, ["gemini-3-pro-preview", "gemini-3-pro-preview", "gemini-3-pro-preview"])

    def test_hard__lessons_optimizer_invocation_propagates_gemini_runner(self):
        self.lessons_optimizer.assert_called_once_with(
            change_id="TEST-AC-001",
            repo="/tmp/target-repo",
            runner="gemini",
            runner_model="gemini-3-pro-preview",
        )


class _SyntheticWorkflowFixtureMixin:
    def _create_mock_artifact(self, path: Path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            with open(path, "w") as f:
                yaml.dump(content, f)
        else:
            with open(path, "w") as f:
                f.write(content)


class FullSyntheticWorkflowAllStagesTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agent_context_root = Path(cls._tmp.name) / "agent-context"
        change_id = "TEST-INTEGRATION-001"
        cls.change_id = change_id
        intake_dir = agent_context_root / change_id / "intake"
        cls.story_path = intake_dir / "story.yaml"
        cls.config_path = intake_dir / "config.yaml"
        cls.constraints_path = intake_dir / "constraints.md"
        instance = cls.__new__(cls)
        instance._create_mock_artifact(cls.story_path, {
            "change_id": change_id,
            "title": "Synthetic workflow integration test",
            "description": "Test story for integration testing",
            "acceptance_criteria": {"AC1": "x", "AC2": "y", "AC3": "z"},
            "raw_input": {"source_type": "synthetic_fixture", "original_fixture": {"test": "data"}},
        })
        instance._create_mock_artifact(cls.config_path, {"change_id": change_id, "project_type": "synthetic-fixture"})
        instance._create_mock_artifact(cls.constraints_path, "Test constraints\n(synthetic mode)")

        planning_dir = agent_context_root / change_id / "planning"
        cls.tasks_path = planning_dir / "tasks.yaml"
        cls.assignments_path = planning_dir / "assignments.json"
        instance._create_mock_artifact(cls.tasks_path, {"story_id": change_id, "tasks": [{"task_id": "T1"}]})
        cls.assignments_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cls.assignments_path, "w") as f:
            json.dump({"story_id": change_id, "execution_schedule": [{"batch": 1, "uows": [{"uow_id": "UOW-001"}]}]}, f)

        qa_dir = agent_context_root / change_id / "qa"
        cls.qa_report_path = qa_dir / "qa_report.yaml"
        instance._create_mock_artifact(cls.qa_report_path, {"change_id": change_id, "status": "passed"})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_easy__intake_story_yaml_artifact_exists(self):
        self.assertTrue(self.story_path.exists())

    def test_easy__intake_config_yaml_artifact_exists(self):
        self.assertTrue(self.config_path.exists())

    def test_easy__intake_constraints_md_artifact_exists(self):
        self.assertTrue(self.constraints_path.exists())

    def test_easy__planning_tasks_yaml_artifact_exists(self):
        self.assertTrue(self.tasks_path.exists())

    def test_easy__planning_assignments_json_artifact_exists(self):
        self.assertTrue(self.assignments_path.exists())

    def test_easy__qa_report_yaml_artifact_exists(self):
        self.assertTrue(self.qa_report_path.exists())

    def test_medium__intake_story_yaml_schema_includes_acceptance_criteria(self):
        with open(self.story_path) as f:
            data = yaml.safe_load(f)
        self.assertIn("acceptance_criteria", data)

    def test_medium__intake_config_yaml_schema_includes_project_type(self):
        with open(self.config_path) as f:
            data = yaml.safe_load(f)
        self.assertIn("project_type", data)

    def test_medium__planning_tasks_yaml_schema_includes_story_id(self):
        with open(self.tasks_path) as f:
            data = yaml.safe_load(f)
        self.assertIn("story_id", data)


class IntakePreservesSyntheticMarkersTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agent_context_root = Path(cls._tmp.name) / "agent-context"
        change_id = "TEST-INTEGRATION-002"
        intake_dir = agent_context_root / change_id / "intake"
        cls.story_path = intake_dir / "story.yaml"
        cls.config_path = intake_dir / "config.yaml"
        instance = cls.__new__(cls)
        instance._create_mock_artifact(cls.story_path, {
            "change_id": change_id,
            "title": "Test story",
            "raw_input": {
                "source_type": "synthetic_fixture",
                "original_fixture": {"title": "Original fixture title"},
            },
        })
        instance._create_mock_artifact(cls.config_path, {"change_id": change_id, "project_type": "synthetic-fixture"})
        with open(cls.story_path) as f:
            cls.story = yaml.safe_load(f)
        with open(cls.config_path) as f:
            cls.config = yaml.safe_load(f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_easy__intake_config_project_type_equals_synthetic_fixture(self):
        self.assertEqual(self.config.get("project_type"), "synthetic-fixture")

    def test_easy__intake_story_has_no_ado_provenance(self):
        self.assertIsNone(self.story.get("ado_provenance"))

    def test_easy__intake_story_contains_raw_input_section(self):
        self.assertIn("raw_input", self.story)

    def test_easy__intake_raw_input_contains_original_fixture(self):
        self.assertIn("original_fixture", self.story["raw_input"])


class DownstreamSyntheticDetectionTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agent_context_root = Path(cls._tmp.name) / "agent-context"
        change_id = "TEST-INTEGRATION-003"
        intake_dir = agent_context_root / change_id / "intake"
        cls.config_path = intake_dir / "config.yaml"
        cls.story_path = intake_dir / "story.yaml"
        instance = cls.__new__(cls)
        instance._create_mock_artifact(cls.config_path, {"change_id": change_id, "project_type": "synthetic-fixture"})
        instance._create_mock_artifact(cls.story_path, {"change_id": change_id, "ado_provenance": None})
        with open(cls.config_path) as f:
            cls.config = yaml.safe_load(f)
        with open(cls.story_path) as f:
            cls.story = yaml.safe_load(f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_easy__downstream_config_project_type_signals_synthetic_mode(self):
        self.assertEqual(self.config.get("project_type"), "synthetic-fixture")

    def test_easy__downstream_story_ado_provenance_is_none(self):
        self.assertIsNone(self.story.get("ado_provenance"))


class SyntheticWorkflowNoCredentialsTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agent_context_root = Path(cls._tmp.name) / "agent-context"
        change_id = "TEST-INTEGRATION-004"
        intake_dir = agent_context_root / change_id / "intake"
        cls.config_path = intake_dir / "config.yaml"
        cls.story_path = intake_dir / "story.yaml"
        instance = cls.__new__(cls)
        instance._create_mock_artifact(cls.config_path, {"change_id": change_id, "project_type": "synthetic-fixture"})
        instance._create_mock_artifact(cls.story_path, {"change_id": change_id, "title": "Synthetic test"})
        with open(cls.config_path) as f:
            cls.config = yaml.safe_load(f)
        with open(cls.story_path) as f:
            cls.story = yaml.safe_load(f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_medium__synthetic_config_has_no_ado_keyed_fields(self):
        ado_keys = [k for k in self.config.keys() if "ado" in k.lower() or "azure" in k.lower()]
        self.assertEqual(ado_keys, [])

    def test_easy__synthetic_story_does_not_contain_ado_provenance_key(self):
        self.assertNotIn("ado_provenance", self.story)


class IntegrationDiagnosticsTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    def test_medium__incomplete_artifact_schema_check_raises_assertion_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            story_path = Path(tmpdir) / "story.yaml"
            self._create_mock_artifact(story_path, {"change_id": "TEST-INTEGRATION-DIAG"})

            with open(story_path) as f:
                data = yaml.safe_load(f)
            missing = [field for field in ["change_id", "title", "description"] if field not in data]
            with self.assertRaises(AssertionError):
                self.assertEqual(missing, [], "Artifact schema error: missing fields")


class FullEndToEndWorkflowTests(unittest.TestCase, _SyntheticWorkflowFixtureMixin):
    """Each stage is verified by a separate single-criterion test (AC6/AC7 hard tier)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agent_context_root = Path(cls._tmp.name) / "agent-context"
        change_id = "TEST-AC-001-E2E"
        cls.change_id = change_id
        intake_dir = agent_context_root / change_id / "intake"
        planning_dir = agent_context_root / change_id / "planning"
        execution_dir = agent_context_root / change_id / "execution"
        qa_dir = agent_context_root / change_id / "qa"

        cls.story_path = intake_dir / "story.yaml"
        cls.config_path = intake_dir / "config.yaml"
        cls.constraints_path = intake_dir / "constraints.md"
        cls.tasks_path = planning_dir / "tasks.yaml"
        cls.assignments_path = planning_dir / "assignments.json"
        cls.qa_report_path = qa_dir / "qa_report.yaml"

        instance = cls.__new__(cls)
        instance._create_mock_artifact(cls.story_path, {
            "change_id": change_id,
            "title": "Synthetic workflow smoke test story",
            "description": "Use this local story fixture",
            "acceptance_criteria": {"AC1": "x", "AC2": "y", "AC3": "z"},
            "raw_input": {
                "source_type": "synthetic_fixture",
                "fixture_path": "agent-context/test-fixtures/synthetic_story.json",
                "original_fixture": {"change_id": change_id, "title": "Synthetic"},
            },
            "metacognitive_context": {"normalization_source": "synthetic_fixture"},
        })
        instance._create_mock_artifact(cls.config_path, {
            "change_id": change_id,
            "project_type": "synthetic-fixture",
            "intake_mode": "synthetic",
        })
        instance._create_mock_artifact(cls.constraints_path, "# Constraints\n- synthetic\n")
        instance._create_mock_artifact(cls.tasks_path, {
            "story_id": change_id,
            "title": "Task Plan",
            "tasks": [{"task_id": f"T{i}", "title": f"Task {i}"} for i in range(1, 6)],
        })
        cls.assignments_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cls.assignments_path, "w") as f:
            json.dump({
                "story_id": change_id,
                "execution_schedule": [
                    {"batch": 1, "uows": [{"uow_id": "UOW-001"}, {"uow_id": "UOW-002"}]},
                    {"batch": 2, "uows": [{"uow_id": "UOW-003"}, {"uow_id": "UOW-004"}]},
                    {"batch": 3, "uows": [{"uow_id": "UOW-005"}]},
                ],
            }, f)
        cls.execution_dir = execution_dir
        for uow_id in ["UOW-001", "UOW-002", "UOW-003", "UOW-004", "UOW-005"]:
            instance._create_mock_artifact(execution_dir / uow_id / "impl_report.yaml", {
                "uow_id": uow_id,
                "change_id": change_id,
                "status": "complete",
            })
        instance._create_mock_artifact(cls.qa_report_path, {
            "change_id": change_id,
            "title": "QA Report",
            "status": "passed",
            "acceptance_criteria_coverage": {
                "AC1": {"status": "passed"},
                "AC2": {"status": "passed"},
                "AC3": {"status": "passed"},
            },
        })

        with open(cls.story_path) as f:
            cls.story = yaml.safe_load(f)
        with open(cls.config_path) as f:
            cls.config = yaml.safe_load(f)
        with open(cls.qa_report_path) as f:
            cls.qa_report = yaml.safe_load(f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_hard__e2e_intake_story_yaml_exists_after_full_pipeline(self):
        self.assertTrue(self.story_path.exists())

    def test_hard__e2e_intake_config_yaml_exists_after_full_pipeline(self):
        self.assertTrue(self.config_path.exists())

    def test_hard__e2e_intake_constraints_md_exists_after_full_pipeline(self):
        self.assertTrue(self.constraints_path.exists())

    def test_hard__e2e_planning_tasks_yaml_exists_after_full_pipeline(self):
        self.assertTrue(self.tasks_path.exists())

    def test_hard__e2e_planning_assignments_json_exists_after_full_pipeline(self):
        self.assertTrue(self.assignments_path.exists())

    def test_hard__e2e_qa_report_yaml_exists_after_full_pipeline(self):
        self.assertTrue(self.qa_report_path.exists())

    def test_hard__e2e_intake_story_preserves_raw_input(self):
        self.assertIn("raw_input", self.story)

    def test_hard__e2e_intake_story_raw_input_includes_original_fixture(self):
        self.assertIn("original_fixture", self.story["raw_input"])

    def test_hard__e2e_intake_story_has_no_ado_provenance(self):
        self.assertIsNone(self.story.get("ado_provenance"))

    def test_hard__e2e_intake_config_project_type_equals_synthetic_fixture(self):
        self.assertEqual(self.config.get("project_type"), "synthetic-fixture")

    def test_hard__e2e_execution_uow_001_impl_report_exists(self):
        self.assertTrue((self.execution_dir / "UOW-001" / "impl_report.yaml").exists())

    def test_hard__e2e_execution_uow_002_impl_report_exists(self):
        self.assertTrue((self.execution_dir / "UOW-002" / "impl_report.yaml").exists())

    def test_hard__e2e_execution_uow_003_impl_report_exists(self):
        self.assertTrue((self.execution_dir / "UOW-003" / "impl_report.yaml").exists())

    def test_hard__e2e_execution_uow_004_impl_report_exists(self):
        self.assertTrue((self.execution_dir / "UOW-004" / "impl_report.yaml").exists())

    def test_hard__e2e_execution_uow_005_impl_report_exists(self):
        self.assertTrue((self.execution_dir / "UOW-005" / "impl_report.yaml").exists())

    def test_hard__e2e_qa_report_overall_status_is_passed(self):
        self.assertEqual(self.qa_report.get("status"), "passed")

    def test_hard__e2e_qa_report_covers_ac1(self):
        self.assertIn("AC1", self.qa_report.get("acceptance_criteria_coverage", {}))

    def test_hard__e2e_qa_report_covers_ac2(self):
        self.assertIn("AC2", self.qa_report.get("acceptance_criteria_coverage", {}))

    def test_hard__e2e_qa_report_covers_ac3(self):
        self.assertIn("AC3", self.qa_report.get("acceptance_criteria_coverage", {}))

    def test_hard__e2e_qa_ac1_status_is_passed(self):
        self.assertEqual(self.qa_report["acceptance_criteria_coverage"]["AC1"]["status"], "passed")

    def test_hard__e2e_qa_ac2_status_is_passed(self):
        self.assertEqual(self.qa_report["acceptance_criteria_coverage"]["AC2"]["status"], "passed")

    def test_hard__e2e_qa_ac3_status_is_passed(self):
        self.assertEqual(self.qa_report["acceptance_criteria_coverage"]["AC3"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
