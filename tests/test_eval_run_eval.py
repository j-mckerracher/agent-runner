import json
import shutil
import subprocess
import threading
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from eval import run_eval
from eval.yaml_io import dump_yaml


class EvalRunEvalTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(__file__).parent / ".run_eval_workdir"
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir()
        self.repo = self.workdir / "repo"
        self.repo.mkdir()
        self.suite_dir = self.workdir / "suite"
        self.suite_dir.mkdir()
        self.baselines = self.workdir / "baselines"
        self.fixtures = self.workdir / "fixtures"
        self.repo_root_patcher = mock.patch.object(run_eval, "REPO_ROOT", self.workdir)
        self.baseline_patcher = mock.patch.object(run_eval, "BASELINE_DIR", self.baselines)
        self.fixture_patcher = mock.patch.object(run_eval, "TRANSIENT_FIXTURE_DIR", self.fixtures)
        self.repo_root_patcher.start()
        self.baseline_patcher.start()
        self.fixture_patcher.start()

    def tearDown(self):
        self.fixture_patcher.stop()
        self.baseline_patcher.stop()
        self.repo_root_patcher.stop()
        if self.workdir.exists():
            shutil.rmtree(self.workdir)

    def _write_story(
        self,
        *,
        expected="needle",
        story_id="story_001",
        change_id="CHG-001",
        suite_tier="hard",
        check_difficulty="high",
    ):
        story_path = self.suite_dir / f"{story_id}.yaml"
        dump_yaml(
            {
                "story_id": story_id,
                "change_id": change_id,
                "title": "Story 001",
                "description": "Implement the needle behavior.",
                "suite_tier": suite_tier,
                "dataset_id": "dataset",
                "acceptance_criteria": [
                    {
                        "ac_id": "AC1",
                        "tier": suite_tier,
                        "text": "Output contains expected text",
                        "check": {
                            "id": "contains_expected",
                            "label": "Contains expected",
                            "mechanism": "contains",
                            "subject": "agent_output",
                            "expected": expected,
                            "difficulty": check_difficulty,
                        },
                    }
                ],
            },
            story_path,
        )
        return story_path

    def _write_suite(self, story_path):
        manifest = self.suite_dir / "suite_manifest.yaml"
        dump_yaml(
            {
                "suite_id": "hard-suite",
                "suite_tier": "hard",
                "dataset_id": "dataset",
                "stories": [story_path.name],
                "total_checks": 1,
            },
            manifest,
        )
        return manifest

    def _write_artifact(self, change_id="CHG-001", text="implemented needle"):
        artifact = self.workdir / "agent-context" / change_id / "execution" / "UOW-001" / "impl_report.yaml"
        artifact.parent.mkdir(parents=True)
        artifact.write_text(text, encoding="utf-8")
        return artifact

    def _args(self, **overrides):
        data = {
            "suite": str(self.suite_dir),
            "story": None,
            "change_id": None,
            "repo": str(self.repo),
            "runner": "claude",
            "model": None,
            "runs": 1,
            "max_concurrent": 1,
            "skip_pipeline": True,
            "skip_materialize": False,
            "skip_opik": True,
            "regression_threshold": 0.0,
            "update_baseline": False,
            "ci": False,
        }
        data.update(overrides)
        return Namespace(**data)

    def test_suite_discovery_story_conversion_and_first_baseline_write(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        self._write_artifact()

        with mock.patch.object(run_eval, "_opik_evaluate") as opik, mock.patch.object(
            run_eval.subprocess, "run"
        ) as subprocess_run:
            result = run_eval.run_eval(self._args())

        self.assertEqual(result.suite_tier, "hard")
        self.assertEqual(len(result.stories), 1)
        self.assertTrue(result.summary.weighted_composite)
        self.assertTrue(result.baseline_written)
        self.assertTrue((self.baselines / "hard.json").exists())
        self.assertFalse(result.opik_logged)
        opik.assert_not_called()
        subprocess_run.assert_not_called()
        self.assertFalse(any(self.fixtures.glob("*.json")))

    def test_pipeline_launch_passes_story_repo_runner_model_and_skip_materialize(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        self._write_artifact(text="")

        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="implemented needle", stderr="")
        with mock.patch.object(run_eval.subprocess, "run", return_value=completed) as subprocess_run:
            result = run_eval.run_eval(
                self._args(skip_pipeline=False, skip_materialize=True, model="claude-sonnet")
            )

        command = subprocess_run.call_args.args[0]
        self.assertIn("--story-file", command)
        self.assertIn("--repo", command)
        self.assertIn(str(self.repo), command)
        self.assertIn("--runner", command)
        self.assertIn("claude", command)
        self.assertIn("--model", command)
        self.assertIn("claude-sonnet", command)
        self.assertIn("--skip-materialize", command)
        self.assertEqual(result.stories[0].subprocess_returncode, 0)

    def test_multi_run_pipeline_uses_isolated_workflow_change_ids(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        seen_change_ids = []
        seen_story_ids = []
        seen_lock = threading.Lock()

        def fake_run(command, **kwargs):
            fixture_path = Path(command[command.index("--story-file") + 1])
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            change_id = fixture["change_id"]
            artifact = (
                self.workdir
                / "agent-context"
                / change_id
                / "execution"
                / "UOW-001"
                / "impl_report.yaml"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_text("implemented needle", encoding="utf-8")
            with seen_lock:
                seen_change_ids.append(change_id)
                seen_story_ids.append(fixture["metadata"]["eval_story_id"])
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

        with mock.patch.object(run_eval.subprocess, "run", side_effect=fake_run) as subprocess_run:
            result = run_eval.run_eval(self._args(skip_pipeline=False, runs=2, max_concurrent=2))

        self.assertEqual(subprocess_run.call_count, 2)
        self.assertCountEqual(seen_change_ids, ["CHG-001-RUN-01", "CHG-001-RUN-02"])
        self.assertEqual(seen_story_ids, ["story_001", "story_001"])
        self.assertCountEqual(
            [story_run.change_id for story_run in result.stories],
            ["CHG-001-RUN-01", "CHG-001-RUN-02"],
        )
        self.assertTrue(all(story_run.story.story_id == "story_001" for story_run in result.stories))
        self.assertFalse((self.workdir / "agent-context" / "CHG-001").exists())
        self.assertTrue(result.summary.weighted_composite)

    def test_skip_pipeline_multi_run_preserves_base_change_id_artifacts(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        self._write_artifact(change_id="CHG-001", text="implemented needle")

        with mock.patch.object(run_eval.subprocess, "run") as subprocess_run:
            result = run_eval.run_eval(self._args(skip_pipeline=True, runs=2, max_concurrent=2))

        subprocess_run.assert_not_called()
        self.assertEqual([story_run.change_id for story_run in result.stories], ["CHG-001", "CHG-001"])
        self.assertFalse(any(self.fixtures.glob("*.json")))
        self.assertTrue(result.summary.weighted_composite)

    def test_pipeline_failure_returns_nonzero_and_does_not_write_baseline(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="implemented needle", stderr="boom")

        with mock.patch.object(run_eval.subprocess, "run", return_value=completed):
            result = run_eval.run_eval(self._args(skip_pipeline=False))

        self.assertTrue(result.workflow_failed)
        self.assertFalse(result.baseline_written)
        self.assertFalse(result.baseline_updated)
        self.assertFalse((self.baselines / "hard.json").exists())

        with mock.patch.object(run_eval.subprocess, "run", return_value=completed):
            code = run_eval.main(
                [
                    "--suite",
                    str(self.suite_dir),
                    "--repo",
                    str(self.repo),
                    "--skip-opik",
                ]
            )

        self.assertEqual(code, 1)
        self.assertFalse((self.baselines / "hard.json").exists())

    def test_single_story_uses_story_suite_tier_for_baseline(self):
        story_path = self._write_story()
        self._write_artifact()

        result = run_eval.run_eval(self._args(suite=None, story=str(story_path)))

        self.assertEqual(result.suite_tier, "hard")
        self.assertTrue(result.baseline_written)
        self.assertEqual(result.baseline_path, self.baselines / "hard.json")
        self.assertTrue((self.baselines / "hard.json").exists())
        self.assertFalse((self.baselines / "single.json").exists())

    def test_single_story_uses_metadata_suite_tier_for_baseline(self):
        story_path = self.suite_dir / "metadata-tier.yaml"
        dump_yaml(
            {
                "story_id": "metadata_tier",
                "change_id": "CHG-META",
                "title": "Metadata tier",
                "description": "Implement the metadata tier behavior.",
                "metadata": {"suite_tier": "medium"},
                "acceptance_criteria": [
                    {
                        "ac_id": "AC1",
                        "tier": "medium",
                        "text": "Output contains expected text",
                        "check": {
                            "id": "contains_expected",
                            "label": "Contains expected",
                            "mechanism": "contains",
                            "subject": "agent_output",
                            "expected": "needle",
                            "difficulty": "medium",
                        },
                    }
                ],
            },
            story_path,
        )
        self._write_artifact(change_id="CHG-META")

        result = run_eval.run_eval(self._args(suite=None, story=str(story_path)))

        self.assertEqual(result.suite_tier, "medium")
        self.assertEqual(result.baseline_path, self.baselines / "medium.json")
        self.assertTrue((self.baselines / "medium.json").exists())

    def test_generated_story_json_uses_suite_yaml_for_checks_and_baseline_tier(self):
        story_path = self._write_story(
            expected="suite needle",
            story_id="generated_medium",
            change_id="CHG-GENERATED",
            suite_tier="medium",
            check_difficulty="medium",
        )
        stories_dir = self.workdir / "eval" / "stories"
        stories_dir.mkdir(parents=True)
        story_json = stories_dir / "generated_medium.json"
        story_json.write_text(
            json.dumps(
                {
                    "change_id": "CHG-GENERATED",
                    "title": "Generated JSON",
                    "description": "Workflow-compatible generated fixture.",
                    "acceptance_criteria": ["Workflow text without check definitions"],
                    "metadata": {
                        "eval_story_id": "generated_medium",
                        "suite_tier": "hard",
                        "dataset_id": "dataset",
                    },
                    "raw_metadata": {"suite_yaml": str(story_path.relative_to(self.workdir))},
                }
            ),
            encoding="utf-8",
        )
        self._write_artifact(change_id="CHG-GENERATED", text="implemented suite needle")

        result = run_eval.run_eval(self._args(suite=None, story=str(story_json)))

        self.assertEqual(result.suite_tier, "medium")
        self.assertEqual(result.baseline_path, self.baselines / "medium.json")
        self.assertTrue((self.baselines / "medium.json").exists())
        self.assertFalse((self.baselines / "hard.json").exists())
        self.assertEqual(result.summary.total_checks, 1)
        self.assertEqual(result.stories[0].checks[0].check_id, "contains_expected")
        self.assertTrue(result.stories[0].checks[0].passed)

    def test_regression_breach_returns_nonzero_status(self):
        story_path = self._write_story(expected="missing")
        self._write_suite(story_path)
        self._write_artifact(text="implemented without expected token")
        self.baselines.mkdir()
        (self.baselines / "hard.json").write_text(
            json.dumps({"suite_tier": "hard", "summary": {"weighted_composite": 1.0}}),
            encoding="utf-8",
        )

        code = run_eval.main(
            [
                "--suite",
                str(self.suite_dir),
                "--repo",
                str(self.repo),
                "--skip-pipeline",
                "--skip-opik",
                "--regression-threshold",
                "0.1",
            ]
        )

        self.assertEqual(code, 1)

    def test_skip_opik_false_logs_with_evaluate_adapter(self):
        story_path = self._write_story()
        self._write_suite(story_path)
        self._write_artifact()
        client = mock.Mock()
        dataset = mock.Mock()

        with mock.patch.object(run_eval, "_create_opik_dataset", return_value=(client, dataset)) as create_dataset, mock.patch.object(
            run_eval, "_opik_evaluate"
        ) as opik:
            result = run_eval.run_eval(self._args(skip_opik=False))

        self.assertTrue(result.opik_logged)
        create_dataset.assert_called_once()
        opik.assert_called_once()
        self.assertIs(opik.call_args.kwargs["dataset"], dataset)
        self.assertEqual(opik.call_args.kwargs["experiment_name"].split("_suite_")[0], "hard")
        client.flush.assert_called_once()
        metric = opik.call_args.kwargs["scoring_metrics"][0]
        self.assertEqual(metric.score()[0].name, "contains_expected_agent_output")

    def test_change_id_compatibility_finds_eval_story_json(self):
        stories_dir = self.workdir / "eval" / "stories"
        stories_dir.mkdir(parents=True)
        story_json = stories_dir / "CHG-JSON.json"
        story_json.write_text(
            json.dumps(
                {
                    "change_id": "CHG-JSON",
                    "title": "JSON story",
                    "description": "JSON description",
                    "acceptance_criteria": ["Contains json needle"],
                    "metadata": {"eval_story_id": "CHG-JSON", "suite_tier": "hard"},
                }
            ),
            encoding="utf-8",
        )

        paths, suite, tier = run_eval.discover_story_paths(change_id="CHG-JSON")

        self.assertEqual(paths, [story_json])
        self.assertIsNone(suite)
        self.assertEqual(tier, "single")


if __name__ == "__main__":
    unittest.main()
