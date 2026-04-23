import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval import run_eval


class _FakeFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _FakeExecutor:
    last_max_workers = None

    def __init__(self, max_workers):
        self.max_workers = max_workers
        type(self).last_max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        return _FakeFuture(fn(*args, **kwargs))


class RunEvalTests(unittest.TestCase):
    def _make_args(self, **overrides):
        base = {
            "change_id": "EVAL-001",
            "mono_root": "/tmp/mono",
            "runner": "claude",
            "gemini_model": "gemini-2.5-flash",
            "runs": 1,
            "max_concurrent": 1,
            "testing_branch": run_eval.DEFAULT_TESTING_BRANCH,
            "experiment_name": None,
            "skip_pipeline": False,
            "skip_materialize": False,
            "skip_opik": True,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_create_temp_story_fixture_suffixes_change_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_story_path = Path(temp_dir) / "story.json"
            source_story_path.write_text(
                json.dumps(
                    {
                        "change_id": "EVAL-001",
                        "title": "Example",
                        "description": "Example story",
                        "acceptance_criteria": ["AC1"],
                    }
                ),
                encoding="utf-8",
            )

            temp_story_path, run_change_id = run_eval._create_temp_story_fixture(source_story_path, 3)
            try:
                temp_story = json.loads(temp_story_path.read_text(encoding="utf-8"))
            finally:
                run_eval._cleanup_temp_story_fixture(temp_story_path)

        self.assertEqual(run_change_id, "EVAL-001-RUN-03")
        self.assertEqual(temp_story["change_id"], "EVAL-001-RUN-03")
        self.assertEqual(temp_story["title"], "Example")
        self.assertEqual(temp_story["acceptance_criteria"], ["AC1"])

    def test_run_evaluations_preserves_single_run_flow(self):
        args = self._make_args(runs=1, max_concurrent=5)
        story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        story_path = Path("/tmp/story.json")

        with (
            patch.object(run_eval, "_run_single_evaluation", return_value={"run_index": 1}) as single_run,
            patch.object(run_eval, "_run_isolated_evaluation") as isolated_run,
        ):
            results = run_eval.run_evaluations(args, story, story_path)

        single_run.assert_called_once_with(args, story, story_path)
        isolated_run.assert_not_called()
        self.assertEqual(results, [{"run_index": 1}])

    def test_run_evaluations_dispatches_isolated_runs_with_capped_parallelism(self):
        args = self._make_args(runs=3, max_concurrent=2)
        story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        story_path = Path("/tmp/story.json")
        seen_run_indexes = []

        def fake_isolated_run(passed_args, passed_story, passed_story_path, run_index):
            seen_run_indexes.append(run_index)
            self.assertIs(passed_args, args)
            self.assertIs(passed_story, story)
            self.assertEqual(passed_story_path, story_path)
            return {
                "run_index": run_index,
                "score": 100 - run_index,
                "passing": 4,
                "total": 5,
                "pipeline_exit_code": 0,
            }

        with (
            patch.object(run_eval, "_run_single_evaluation") as single_run,
            patch.object(run_eval, "_run_isolated_evaluation", side_effect=fake_isolated_run) as isolated_run,
            patch.object(run_eval, "ThreadPoolExecutor", _FakeExecutor),
            patch.object(run_eval, "as_completed", side_effect=lambda futures: list(futures)),
        ):
            results = run_eval.run_evaluations(args, story, story_path)

        single_run.assert_not_called()
        self.assertEqual(isolated_run.call_count, 3)
        self.assertEqual(seen_run_indexes, [1, 2, 3])
        self.assertEqual(_FakeExecutor.last_max_workers, 2)
        self.assertEqual([result["run_index"] for result in results], [1, 2, 3])

    def test_positive_int_rejects_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            run_eval._positive_int("0")

    def test_run_pipeline_passes_gemini_runner_to_run_py(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            story_path = Path(temp_dir) / "story.json"
            story_path.write_text(
                json.dumps(
                    {
                        "change_id": "EVAL-001",
                        "title": "Example",
                        "description": "Example story",
                        "acceptance_criteria": ["AC1"],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(run_eval.subprocess, "run", return_value=argparse.Namespace(returncode=0)) as run_subprocess:
                exit_code = run_eval._run_pipeline(
                    change_id="EVAL-001",
                    mono_root="/tmp/mono",
                    runner="gemini",
                    skip_materialize=False,
                    gemini_model="gemini-3-pro-preview",
                    story_path=story_path,
                )

        self.assertEqual(exit_code, 0)
        called_cmd = run_subprocess.call_args.args[0]
        self.assertEqual(called_cmd[0], run_eval.sys.executable)
        self.assertIn("--runner", called_cmd)
        runner_index = called_cmd.index("--runner")
        self.assertEqual(called_cmd[runner_index + 1], "gemini")
        self.assertIn("--gemini-model", called_cmd)
        model_index = called_cmd.index("--gemini-model")
        self.assertEqual(called_cmd[model_index + 1], "gemini-3-pro-preview")


if __name__ == "__main__":
    unittest.main()
