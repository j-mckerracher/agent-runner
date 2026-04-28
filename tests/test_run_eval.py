# Difficulty rubric for this file:
#   easy   = single-field assertion on a return value or a temp-fixture file.
#   medium = mock dispatch verification (which helper was called, with what
#            kwargs, against what argv slice).
#   hard   = (none in this file)

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


def _make_args(**overrides):
    base = {
        "change_id": "EVAL-001",
        "mono_root": "/tmp/mono",
        "runner": "claude",
        "model": "gemini-2.5-flash",
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


class CreateTempStoryFixtureTests(unittest.TestCase):
    def _create_temp_and_load(self):
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
        return run_change_id, temp_story

    def test_easy__temp_fixture_run_change_id_has_run_03_suffix(self):
        run_change_id, _ = self._create_temp_and_load()
        self.assertEqual(run_change_id, "EVAL-001-RUN-03")

    def test_easy__temp_fixture_change_id_field_matches_run_change_id(self):
        _, temp_story = self._create_temp_and_load()
        self.assertEqual(temp_story["change_id"], "EVAL-001-RUN-03")

    def test_easy__temp_fixture_preserves_title_field(self):
        _, temp_story = self._create_temp_and_load()
        self.assertEqual(temp_story["title"], "Example")

    def test_easy__temp_fixture_preserves_acceptance_criteria_list(self):
        _, temp_story = self._create_temp_and_load()
        self.assertEqual(temp_story["acceptance_criteria"], ["AC1"])


class RunEvaluationsDispatchTests(unittest.TestCase):
    def test_medium__single_run_invokes_run_single_evaluation_once(self):
        args = _make_args(runs=1, max_concurrent=5)
        story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        story_path = Path("/tmp/story.json")

        with (
            patch.object(run_eval, "_run_single_evaluation", return_value={"run_index": 1}) as single_run,
            patch.object(run_eval, "_run_isolated_evaluation"),
        ):
            run_eval.run_evaluations(args, story, story_path)

        single_run.assert_called_once_with(args, story, story_path)

    def test_medium__single_run_does_not_invoke_isolated_runner(self):
        args = _make_args(runs=1, max_concurrent=5)
        story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        story_path = Path("/tmp/story.json")

        with (
            patch.object(run_eval, "_run_single_evaluation", return_value={"run_index": 1}),
            patch.object(run_eval, "_run_isolated_evaluation") as isolated_run,
        ):
            run_eval.run_evaluations(args, story, story_path)

        isolated_run.assert_not_called()

    def test_easy__single_run_results_list_passes_through_single_run_result(self):
        args = _make_args(runs=1, max_concurrent=5)
        story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        story_path = Path("/tmp/story.json")

        with (
            patch.object(run_eval, "_run_single_evaluation", return_value={"run_index": 1}),
            patch.object(run_eval, "_run_isolated_evaluation"),
        ):
            results = run_eval.run_evaluations(args, story, story_path)

        self.assertEqual(results, [{"run_index": 1}])


class RunEvaluationsIsolatedRunsTests(unittest.TestCase):
    def setUp(self):
        self.args = _make_args(runs=3, max_concurrent=2)
        self.story = {"title": "Example", "acceptance_criteria": ["AC1"]}
        self.story_path = Path("/tmp/story.json")
        self.seen_run_indexes: list[int] = []

        def fake_isolated_run(passed_args, passed_story, passed_story_path, run_index):
            self.seen_run_indexes.append(run_index)
            return {
                "run_index": run_index,
                "score": 100 - run_index,
                "passing": 4,
                "total": 5,
                "pipeline_exit_code": 0,
            }

        self._fake_isolated_run = fake_isolated_run

    def _run(self):
        with (
            patch.object(run_eval, "_run_single_evaluation") as single_run,
            patch.object(run_eval, "_run_isolated_evaluation", side_effect=self._fake_isolated_run) as isolated_run,
            patch.object(run_eval, "ThreadPoolExecutor", _FakeExecutor),
            patch.object(run_eval, "as_completed", side_effect=lambda futures: list(futures)),
        ):
            results = run_eval.run_evaluations(self.args, self.story, self.story_path)
        return single_run, isolated_run, results

    def test_medium__multi_run_does_not_invoke_single_runner(self):
        single_run, _, _ = self._run()
        single_run.assert_not_called()

    def test_medium__multi_run_invokes_isolated_runner_three_times(self):
        _, isolated_run, _ = self._run()
        self.assertEqual(isolated_run.call_count, 3)

    def test_medium__multi_run_dispatches_run_indexes_one_through_three(self):
        self._run()
        self.assertEqual(self.seen_run_indexes, [1, 2, 3])

    def test_medium__multi_run_caps_executor_max_workers_at_two(self):
        self._run()
        self.assertEqual(_FakeExecutor.last_max_workers, 2)

    def test_medium__multi_run_results_preserve_run_indexes(self):
        _, _, results = self._run()
        self.assertEqual([result["run_index"] for result in results], [1, 2, 3])


class PositiveIntTests(unittest.TestCase):
    def test_easy__positive_int_rejects_zero_with_argparse_error(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            run_eval._positive_int("0")


class RunPipelineGeminiPassthroughTests(unittest.TestCase):
    def _run_pipeline(self):
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
                    model="gemini-3-pro-preview",
                    story_path=story_path,
                )
        return exit_code, run_subprocess.call_args.args[0]

    def test_easy__run_pipeline_returns_subprocess_exit_code_zero(self):
        exit_code, _ = self._run_pipeline()
        self.assertEqual(exit_code, 0)

    def test_medium__run_pipeline_argv_starts_with_python_executable(self):
        _, called_cmd = self._run_pipeline()
        self.assertEqual(called_cmd[0], run_eval.sys.executable)

    def test_medium__run_pipeline_argv_includes_runner_flag(self):
        _, called_cmd = self._run_pipeline()
        self.assertIn("--runner", called_cmd)

    def test_medium__run_pipeline_argv_passes_gemini_runner(self):
        _, called_cmd = self._run_pipeline()
        runner_index = called_cmd.index("--runner")
        self.assertEqual(called_cmd[runner_index + 1], "gemini")

    def test_medium__run_pipeline_argv_includes_model_flag(self):
        _, called_cmd = self._run_pipeline()
        self.assertIn("--model", called_cmd)

    def test_medium__run_pipeline_argv_passes_explicit_model(self):
        _, called_cmd = self._run_pipeline()
        model_index = called_cmd.index("--model")
        self.assertEqual(called_cmd[model_index + 1], "gemini-3-pro-preview")


if __name__ == "__main__":
    unittest.main()
