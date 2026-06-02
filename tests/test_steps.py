"""
Tests for workflow stage helpers in core.steps.

Difficulty rubric for this file:
  easy   = stage dispatch assertions for a single mode.
  medium = synthetic intake artifact verification across multiple files and fields.
  hard   = (none in this file)
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from core.steps import (
    build_intake_prompt,
    _create_ado_pull_request,
    _write_synthetic_intake_artifacts,
    step_pr_review,
    step_intake,
    step_task_assigner,
    step_task_gen_producer,
)


def _write_fixture(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class SyntheticIntakeArtifactWriterTests(unittest.TestCase):
    def test_medium__writer_creates_canonical_synthetic_intake_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fixture_path = root / "EVAL-003.json"
            fixture = {
                "change_id": "EVAL-003",
                "title": "Enforce maximum date range on order search",
                "description": "Add a 365-day max date span validation.",
                "acceptance_criteria": [
                    "Reject collected date ranges over 365 days.",
                    "Allow exactly 365 days.",
                ],
                "metadata": {"eval_story_id": "EVAL-003"},
            }
            _write_fixture(fixture_path, fixture)

            with patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"):
                summary = _write_synthetic_intake_artifacts(
                    intake_source=str(fixture_path),
                    repo="/tmp/target-repo",
                    change_id="EVAL-003",
                )

            story_path = root / "agent-context" / "EVAL-003" / "intake" / "story.yaml"
            config_path = root / "agent-context" / "EVAL-003" / "intake" / "config.yaml"
            constraints_path = root / "agent-context" / "EVAL-003" / "intake" / "constraints.md"

            with story_path.open("r", encoding="utf-8") as handle:
                story = yaml.safe_load(handle)
            with config_path.open("r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            constraints = constraints_path.read_text(encoding="utf-8")

            self.assertEqual(story["change_id"], "EVAL-003")
            self.assertEqual(
                story["acceptance_criteria"],
                {
                    "AC1": "Reject collected date ranges over 365 days.",
                    "AC2": "Allow exactly 365 days.",
                },
            )
            self.assertEqual(story["raw_input"]["source_type"], "synthetic_fixture")
            self.assertEqual(story["raw_input"]["fixture_path"], str(fixture_path.resolve()))
            self.assertEqual(json.loads(story["raw_input"]["original_fixture"]), fixture)
            self.assertIsNone(story["ado_provenance"])
            self.assertEqual(config["project_type"], "synthetic-fixture")
            self.assertEqual(config["intake_mode"], "synthetic")
            self.assertEqual(config["run_metadata"]["current_stage"], "intake")
            self.assertEqual(
                config["run_metadata"]["feature_branch"],
                "feature/eval-003-enforce-maximum-date-range-on",
            )
            self.assertIn("synthetic fixture", constraints.lower())
            self.assertIn(str(fixture_path.resolve()), constraints)
            self.assertIn(config["run_metadata"]["feature_branch"], constraints)
            self.assertIn("normalized 2 acceptance criteria", summary)


class StepIntakeSyntheticModeTests(unittest.TestCase):
    def test_easy__ado_prompt_includes_prepared_feature_branch(self):
        prompt = build_intake_prompt(
            intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
            repo="/tmp/target-repo",
            change_id="WI-123456",
            intake_mode="ado",
            runner="copilot",
            feature_branch="feature/test-branch",
        )

        self.assertIn("Prepared feature branch: feature/test-branch", prompt)
        self.assertIn("Set run_metadata.feature_branch in config.yaml exactly", prompt)

    def test_easy__synthetic_mode_bypasses_llm_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fixture_path = root / "story.json"
            _write_fixture(
                fixture_path,
                {
                    "change_id": "TEST-AC-123",
                    "title": "Synthetic intake writer",
                    "description": "Verify step_intake uses deterministic synthetic artifact generation.",
                    "acceptance_criteria": ["Create the intake artifacts."],
                },
            )

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.run_agent_cmd") as run_agent_cmd,
            ):
                result = step_intake(
                    intake_source=str(fixture_path),
                    repo="/tmp/target-repo",
                    change_id="TEST-AC-123",
                    intake_mode="synthetic",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            run_agent_cmd.assert_not_called()
            self.assertIn("Created synthetic intake artifacts", result)
            self.assertTrue((root / "agent-context" / "TEST-AC-123" / "intake" / "story.yaml").is_file())

    def test_easy__synthetic_mode_uses_supplied_feature_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fixture_path = root / "story.json"
            _write_fixture(
                fixture_path,
                {
                    "change_id": "TEST-AC-123",
                    "title": "Synthetic intake writer",
                    "description": "Verify supplied branch is preserved.",
                    "acceptance_criteria": ["Create the intake artifacts."],
                },
            )

            with patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"):
                step_intake(
                    intake_source=str(fixture_path),
                    repo="/tmp/target-repo",
                    change_id="TEST-AC-123",
                    intake_mode="synthetic",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                    feature_branch="feature/test-branch",
                )

            config_path = root / "agent-context" / "TEST-AC-123" / "intake" / "config.yaml"
            with config_path.open("r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            self.assertEqual(config["run_metadata"]["feature_branch"], "feature/test-branch")

    def test_easy__ado_mode_still_uses_llm_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            def _fake_runner(**kwargs):
                story_path = root / "agent-context" / "WI-123456" / "intake" / "story.yaml"
                config_path = root / "agent-context" / "WI-123456" / "intake" / "config.yaml"
                story_path.parent.mkdir(parents=True, exist_ok=True)
                story_path.write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "WI-123456",
                            "title": "stub",
                            "description": "stub",
                            "acceptance_criteria": {"AC1": "stub criterion"},
                        }
                    ),
                    encoding="utf-8",
                )
                config_path.write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "WI-123456",
                            "code_repo": "/tmp/target-repo",
                            "custom": {"preserved": True},
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                return "intake complete"

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.run_agent_cmd", side_effect=_fake_runner) as run_agent_cmd,
                patch("core.user_escalation.request_user_input", side_effect=RuntimeError("no interactive channel")),
            ):
                result = step_intake(
                    intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
                    repo="/tmp/target-repo",
                    change_id="WI-123456",
                    intake_mode="ado",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                    feature_branch="feature/test-branch",
                )

            run_agent_cmd.assert_called_once()
            self.assertEqual(result, "intake complete")
            config_path = root / "agent-context" / "WI-123456" / "intake" / "config.yaml"
            with config_path.open("r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            self.assertEqual(config["run_metadata"]["feature_branch"], "feature/test-branch")
            self.assertEqual(config["custom"], {"preserved": True})

    def test_medium__ado_mode_surfaces_refusal_when_no_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            logs_dir = root / "logs"
            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.logs_root", return_value=logs_dir),
                patch(
                    "core.steps.run_agent_cmd",
                    return_value="I'm sorry, but I cannot assist with that request.",
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"exited without writing required artifact.*refusal_suspected=True",
                ):
                    step_intake(
                        intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
                        repo="/tmp/target-repo",
                        change_id="WI-123456",
                        intake_mode="ado",
                        runner="copilot",
                        runner_model="gpt-5-mini",
                    )

    def test_medium__ado_mode_raises_with_listing_when_agent_writes_wrong_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            logs_dir = root / "logs"
            intake_dir = root / "agent-context" / "WI-123456" / "intake"
            response_text = "Here is a story summary\nnothing structured."

            def _fake_runner(**kwargs):
                intake_dir.mkdir(parents=True, exist_ok=True)
                (intake_dir / "story.md").write_text("# story\n", encoding="utf-8")
                (intake_dir / "tasks.md").write_text("- task 1\n", encoding="utf-8")
                return response_text

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.logs_root", return_value=logs_dir),
                patch("core.steps.run_agent_cmd", side_effect=_fake_runner),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    step_intake(
                        intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
                        repo="/tmp/target-repo",
                        change_id="WI-123456",
                        intake_mode="ado",
                        runner="copilot",
                        runner_model="gpt-5-mini",
                    )

            message = str(ctx.exception)
            self.assertIn("story.yaml", message)
            self.assertIn("story.md", message)
            self.assertIn("tasks.md", message)
            self.assertIn("refusal_suspected=False", message)

            transcripts = list((logs_dir / "intake").glob("WI-123456_*_response.txt"))
            self.assertEqual(len(transcripts), 1)
            self.assertEqual(transcripts[0].read_text(encoding="utf-8"), response_text)


class StepIntakeManualModeTests(unittest.TestCase):
    def test_easy__manual_mode_bypasses_llm_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            story_path = root / "manual_story.json"
            _write_fixture(
                story_path,
                {
                    "work_item_id": "123456",
                    "title": "Manual intake writer",
                    "description": "Verify step_intake uses deterministic manual artifact generation.",
                    "acceptance_criteria": "- Create the intake artifacts.\n- Preserve the original payload.",
                },
            )

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.run_agent_cmd") as run_agent_cmd,
            ):
                result = step_intake(
                    intake_source=str(story_path),
                    repo="/tmp/target-repo",
                    change_id="WI-123456",
                    intake_mode="manual",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            run_agent_cmd.assert_not_called()
            self.assertIn("Created manual intake artifacts", result)
            story_yaml = root / "agent-context" / "WI-123456" / "intake" / "story.yaml"
            self.assertTrue(story_yaml.is_file())
            with story_yaml.open("r", encoding="utf-8") as handle:
                story = yaml.safe_load(handle)
            self.assertEqual(story["raw_input"]["source_type"], "manual_paste")
            self.assertIsNone(story["ado_provenance"])
            self.assertEqual(story["acceptance_criteria"]["AC1"], "Create the intake artifacts.")

    def test_easy__manual_mode_uses_supplied_feature_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            story_path = root / "manual_story.json"
            _write_fixture(
                story_path,
                {
                    "title": "Manual intake writer",
                    "description": "Verify supplied branch is preserved.",
                    "acceptance_criteria": "- Create the intake artifacts.",
                },
            )

            with patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"):
                step_intake(
                    intake_source=str(story_path),
                    repo="/tmp/target-repo",
                    change_id="WI-123456",
                    intake_mode="manual",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                    feature_branch="feature/test-branch",
                )

            config_path = root / "agent-context" / "WI-123456" / "intake" / "config.yaml"
            with config_path.open("r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            self.assertEqual(config["run_metadata"]["feature_branch"], "feature/test-branch")

    def test_easy__manual_mode_skips_acceptance_criteria_confirmation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            story_path = root / "manual_story.json"
            _write_fixture(
                story_path,
                {
                    "title": "Manual intake writer",
                    "description": "Verify confirmation is skipped.",
                    "acceptance_criteria": "- Create the intake artifacts.",
                },
            )

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.user_escalation.request_user_input") as request_user_input,
            ):
                step_intake(
                    intake_source=str(story_path),
                    repo="/tmp/target-repo",
                    change_id="manual-test",
                    intake_mode="manual",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            request_user_input.assert_not_called()


class PullRequestStageTests(unittest.TestCase):
    def _write_pr_stage_artifacts(self, root: Path, change_id: str = "WI-123") -> Path:
        context_root = root / "agent-context"
        intake_dir = context_root / change_id / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        (intake_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "run_metadata": {
                        "feature_branch": "feature/wi-123-review-stage",
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (intake_dir / "story.yaml").write_text(
            yaml.safe_dump(
                {
                    "change_id": change_id,
                    "title": "Review created PR",
                    "acceptance_criteria": {"AC1": "Create a review artifact."},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return context_root

    def _completed(self, command: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    def test_medium__create_ado_pull_request_commits_dirty_worktree_and_targets_develop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            context_root = self._write_pr_stage_artifacts(root)
            commands: list[list[str]] = []

            def fake_run(command, cwd, check, capture_output, text):  # noqa: ARG001
                commands.append(list(command))
                if command[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                    return self._completed(command, "feature/wi-123-review-stage\n")
                if command[:3] == ["git", "status", "--porcelain"]:
                    return self._completed(command, " M src/app.ts\n")
                if command[:4] == ["az", "repos", "pr", "create"]:
                    return self._completed(command, '{"pullRequestId": 42, "url": "https://example/pr/42"}\n')
                return self._completed(command)

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", context_root),
                patch("core.steps.subprocess.run", side_effect=fake_run),
            ):
                payload = _create_ado_pull_request("WI-123", "/tmp/target-repo", "feature/wi-123-review-stage")

            self.assertEqual(payload["pullRequestId"], 42)
            self.assertTrue(payload["committed_dirty_worktree"])
            self.assertIn(["git", "add", "-A"], commands)
            self.assertIn(["git", "commit", "-m", "Implement WI-123"], commands)
            self.assertIn(["git", "push", "-u", "origin", "feature/wi-123-review-stage"], commands)
            az_command = next(command for command in commands if command[:4] == ["az", "repos", "pr", "create"])
            self.assertIn("--target-branch", az_command)
            self.assertEqual(az_command[az_command.index("--target-branch") + 1], "develop")
            pr_json = context_root / "WI-123" / "pr" / "pr.json"
            self.assertEqual(json.loads(pr_json.read_text(encoding="utf-8"))["pullRequestId"], 42)

    def test_medium__create_ado_pull_request_skips_commit_when_worktree_is_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            context_root = self._write_pr_stage_artifacts(root)
            commands: list[list[str]] = []

            def fake_run(command, cwd, check, capture_output, text):  # noqa: ARG001
                commands.append(list(command))
                if command[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                    return self._completed(command, "feature/wi-123-review-stage\n")
                if command[:3] == ["git", "status", "--porcelain"]:
                    return self._completed(command, "")
                if command[:4] == ["az", "repos", "pr", "create"]:
                    return self._completed(command, '{"pullRequestId": 43}\n')
                return self._completed(command)

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", context_root),
                patch("core.steps.subprocess.run", side_effect=fake_run),
            ):
                payload = _create_ado_pull_request("WI-123", "/tmp/target-repo", "feature/wi-123-review-stage")

            self.assertFalse(payload["committed_dirty_worktree"])
            self.assertNotIn(["git", "commit", "-m", "Implement WI-123"], commands)

    def test_medium__step_pr_review_invokes_reviewer_once_and_preserves_markdown_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            context_root = self._write_pr_stage_artifacts(root)
            pr_payload = {"pullRequestId": 44}

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", context_root),
                patch("core.steps._create_ado_pull_request", return_value=pr_payload),
                patch("core.steps.resolve_agent_model", return_value="gpt-test"),
                patch("core.steps.run_agent_cmd", return_value="review response") as run_agent_cmd,
            ):
                review_path = Path(step_pr_review("WI-123", "/tmp/target-repo", runner="copilot", runner_model="gpt-test"))

            run_agent_cmd.assert_called_once()
            self.assertEqual(run_agent_cmd.call_args.kwargs["agent"], "pr-reviewer")
            self.assertTrue(review_path.is_file())
            self.assertIn("review response", review_path.read_text(encoding="utf-8"))


class CopilotPlanningFallbackTests(unittest.TestCase):
    def test_medium__task_gen_producer_writes_fallback_tasks_when_copilot_returns_no_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_context = root / "agent-context"
            story_path = agent_context / "EVAL-003" / "intake" / "story.yaml"
            story_path.parent.mkdir(parents=True, exist_ok=True)
            story_path.write_text(
                yaml.safe_dump(
                    {
                        "change_id": "EVAL-003",
                        "acceptance_criteria": {
                            "AC1": "Implement the requested behavior.",
                            "AC2": "Add automated coverage.",
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", agent_context),
                patch(
                    "core.steps.run_agent_cmd",
                    return_value="I'm sorry, but I cannot assist with that request.",
                ),
            ):
                result = step_task_gen_producer(
                    context=f"Generate a task plan from {agent_context}/EVAL-003/intake/.",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            task_plan_path = agent_context / "EVAL-003" / "planning" / "tasks.yaml"
            with task_plan_path.open("r", encoding="utf-8") as handle:
                task_plan = yaml.safe_load(handle)

            self.assertIn("fallback planning/tasks.yaml", result)
            self.assertEqual([task["id"] for task in task_plan["tasks"]], ["T1", "T2", "T3"])
            self.assertEqual(task_plan["ac_coverage_matrix"]["AC1"], ["T1", "T2", "T3"])

    def test_medium__task_assigner_writes_fallback_assignments_and_uow_specs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_context = root / "agent-context"
            planning_path = agent_context / "EVAL-003" / "planning" / "tasks.yaml"
            planning_path.parent.mkdir(parents=True, exist_ok=True)
            planning_path.write_text(
                yaml.safe_dump(
                    {
                        "story_id": "EVAL-003",
                        "tasks": [
                            {
                                "id": "T1",
                                "title": "Implement the requested change",
                                "description": "Implement the code change.",
                                "ac_mapping": ["AC1"],
                                "dependencies": [],
                            },
                            {
                                "id": "T2",
                                "title": "Add automated coverage",
                                "description": "Add tests.",
                                "ac_mapping": ["AC1"],
                                "dependencies": ["T1"],
                            },
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", agent_context),
                patch(
                    "core.steps.run_agent_cmd",
                    return_value="I'm sorry, but I cannot assist with that request.",
                ),
            ):
                result = step_task_assigner(
                    context=f"Create an execution schedule from {agent_context}/EVAL-003/planning/tasks.yaml.",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            assignments_path = agent_context / "EVAL-003" / "planning" / "assignments.json"
            uow_spec_path = agent_context / "EVAL-003" / "execution" / "UOW-001" / "uow_spec.yaml"

            assignments = json.loads(assignments_path.read_text(encoding="utf-8"))
            self.assertIn("fallback planning/assignments.json", result)
            self.assertEqual([batch["batch_id"] for batch in assignments["batches"]], [1, 2])
            self.assertTrue(uow_spec_path.is_file())

    def test_medium__task_assigner_materializes_uow_specs_for_normal_assignments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            agent_context = root / "agent-context"
            planning_path = agent_context / "EVAL-004" / "planning" / "tasks.yaml"
            planning_path.parent.mkdir(parents=True, exist_ok=True)
            planning_path.write_text(
                yaml.safe_dump(
                    {
                        "story_id": "EVAL-004",
                        "tasks": [
                            {
                                "id": "T1",
                                "title": "Implement the primary behavior",
                                "description": "Update the production code.",
                                "ac_mapping": ["AC1"],
                                "dependencies": [],
                                "priority": "high",
                                "complexity": "simple",
                                "definition_of_done": ["Primary behavior implemented"],
                            },
                            {
                                "id": "T2",
                                "title": "Verify the behavior",
                                "description": "Add tests and verification.",
                                "ac_mapping": ["AC1"],
                                "dependencies": ["T1"],
                                "priority": "medium",
                                "complexity": "moderate",
                                "definition_of_done": ["Coverage added"],
                                "implementation_hints": ["tests/test_feature.py"],
                            },
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            assignments_path = agent_context / "EVAL-004" / "planning" / "assignments.json"

            def _write_assignments(*_args, **_kwargs):
                assignments_path.parent.mkdir(parents=True, exist_ok=True)
                assignments_path.write_text(
                    json.dumps(
                        {
                            "story_id": "EVAL-004",
                            "batches": [
                                {
                                    "batch_id": 1,
                                    "uows": [
                                        {
                                            "uow_id": "UOW-001",
                                            "source_task_id": "T1",
                                            "assigned_role": "software-engineer",
                                            "priority_in_batch": 1,
                                            "rationale": "Implement first",
                                        }
                                    ],
                                    "parallel_execution": False,
                                    "batch_rationale": "Implementation batch",
                                },
                                {
                                    "batch_id": 2,
                                    "uows": [
                                        {
                                            "uow_id": "UOW-002",
                                            "source_task_id": "T2",
                                            "assigned_role": "software-engineer",
                                            "priority_in_batch": 1,
                                            "rationale": "Verify after implementation",
                                        }
                                    ],
                                    "parallel_execution": False,
                                    "batch_rationale": "Verification batch",
                                },
                            ],
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                return "assignments complete"

            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", agent_context),
                patch("core.steps.run_agent_cmd", side_effect=_write_assignments),
            ):
                result = step_task_assigner(
                    context=f"Create an execution schedule from {agent_context}/EVAL-004/planning/tasks.yaml.",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            uow_one = yaml.safe_load(
                (agent_context / "EVAL-004" / "execution" / "UOW-001" / "uow_spec.yaml").read_text(encoding="utf-8")
            )
            uow_two = yaml.safe_load(
                (agent_context / "EVAL-004" / "execution" / "UOW-002" / "uow_spec.yaml").read_text(encoding="utf-8")
            )

            self.assertEqual(result, "assignments complete")
            self.assertEqual(uow_one["title"], "Implement the primary behavior")
            self.assertEqual(uow_one["story_id"], "EVAL-004")
            self.assertEqual(uow_two["dependencies"], ["UOW-001"])
            self.assertEqual(uow_two["definition_of_done"], ["Coverage added"])
            self.assertEqual(uow_two["implementation_hints"], ["tests/test_feature.py"])


if __name__ == "__main__":
    unittest.main()
