"""
Tests for workflow stage helpers in core.steps.

Difficulty rubric for this file:
  easy   = stage dispatch assertions for a single mode.
  medium = synthetic intake artifact verification across multiple files and fields.
  hard   = (none in this file)
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.steps import (
    _write_synthetic_intake_artifacts,
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

    def test_easy__ado_mode_still_uses_llm_runner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch("core.steps.run_agent_cmd", return_value="intake complete") as run_agent_cmd,
            ):
                result = step_intake(
                    intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
                    repo="/tmp/target-repo",
                    change_id="WI-123456",
                    intake_mode="ado",
                    runner="copilot",
                    runner_model="gpt-5-mini",
                )

            run_agent_cmd.assert_called_once()
            self.assertEqual(result, "intake complete")

    def test_medium__ado_mode_surfaces_refusal_when_no_artifacts_are_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                patch("core.steps.AGENT_CONTEXT_ROOT", root / "agent-context"),
                patch(
                    "core.steps.run_agent_cmd",
                    return_value="I'm sorry, but I cannot assist with that request.",
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "returned a refusal"):
                    step_intake(
                        intake_source="https://dev.azure.com/example/project/_workitems/edit/123456",
                        repo="/tmp/target-repo",
                        change_id="WI-123456",
                        intake_mode="ado",
                        runner="copilot",
                        runner_model="gpt-5-mini",
                    )


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


if __name__ == "__main__":
    unittest.main()
