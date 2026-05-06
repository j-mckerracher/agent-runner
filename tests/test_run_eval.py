import argparse
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from eval import run_eval


class RunEvalCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(__file__).parent / ".run_eval_compat_workdir"
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir()
        self.repo = self.workdir / "repo"
        self.repo.mkdir()
        self.fixture_dir = self.workdir / "fixtures"
        self.repo_root_patcher = patch.object(run_eval, "REPO_ROOT", self.workdir)
        self.fixture_patcher = patch.object(run_eval, "TRANSIENT_FIXTURE_DIR", self.fixture_dir)
        self.repo_root_patcher.start()
        self.fixture_patcher.start()

    def tearDown(self):
        self.fixture_patcher.stop()
        self.repo_root_patcher.stop()
        if self.workdir.exists():
            shutil.rmtree(self.workdir)

    def _write_workflow_json(self, change_id="EVAL-001"):
        path = self.workdir / f"{change_id}.json"
        path.write_text(
            json.dumps(
                {
                    "change_id": change_id,
                    "title": "Example",
                    "description": "Example story",
                    "acceptance_criteria": ["AC1"],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_mono_root_alias_populates_repo(self):
        story_path = self._write_workflow_json()

        args = run_eval.build_parser().parse_args(
            ["--story", str(story_path), "--mono-root", str(self.repo), "--skip-opik"]
        )

        self.assertEqual(args.repo, str(self.repo))

    def test_positive_int_rejects_zero_with_argparse_error(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            run_eval._positive_int("0")

    def test_json_fixture_copy_isolates_change_id_without_mutating_source(self):
        source_story_path = self._write_workflow_json()

        copied_path = run_eval._write_json_fixture_copy(source_story_path, 3, "EVAL-001-RUN-03")

        copied_story = json.loads(copied_path.read_text(encoding="utf-8"))
        source_story = json.loads(source_story_path.read_text(encoding="utf-8"))
        self.assertEqual(copied_story["change_id"], "EVAL-001-RUN-03")
        self.assertEqual(copied_story["title"], "Example")
        self.assertEqual(source_story["change_id"], "EVAL-001")
        self.assertEqual(copied_path.parent, self.fixture_dir)

    def test_run_workflow_passes_story_repo_runner_model_and_skip_materialize(self):
        story_path = self._write_workflow_json()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch.object(run_eval.subprocess, "run", return_value=completed) as subprocess_run:
            result = run_eval._run_workflow(
                fixture_path=story_path,
                repo=str(self.repo),
                runner="gemini",
                model="gemini-3-pro-preview",
                skip_materialize=True,
            )

        self.assertIs(result, completed)
        command = subprocess_run.call_args.args[0]
        self.assertEqual(command[0], run_eval.sys.executable)
        self.assertIn("--story-file", command)
        self.assertEqual(command[command.index("--story-file") + 1], str(story_path))
        self.assertEqual(command[command.index("--repo") + 1], str(self.repo))
        self.assertEqual(command[command.index("--runner") + 1], "gemini")
        self.assertEqual(command[command.index("--model") + 1], "gemini-3-pro-preview")
        self.assertIn("--skip-materialize", command)
        self.assertEqual(subprocess_run.call_args.kwargs["cwd"], str(self.workdir))

    def test_run_workflow_streams_live_output_when_requested(self):
        story_path = self._write_workflow_json()
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="live", stderr="")

        with patch.object(run_eval, "_run_subprocess_live", return_value=completed) as run_live, patch.object(
            run_eval.subprocess, "run"
        ) as subprocess_run:
            result = run_eval._run_workflow(
                fixture_path=story_path,
                repo=str(self.repo),
                runner="copilot",
                model="gpt-5.4-mini",
                skip_materialize=False,
                stream_output=True,
            )

        self.assertIs(result, completed)
        subprocess_run.assert_not_called()
        command = run_live.call_args.args[0]
        self.assertEqual(command[0], run_eval.sys.executable)
        self.assertEqual(command[command.index("--story-file") + 1], str(story_path))
        self.assertEqual(command[command.index("--repo") + 1], str(self.repo))
        self.assertEqual(command[command.index("--runner") + 1], "copilot")
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.4-mini")
        self.assertEqual(run_live.call_args.kwargs["cwd"], str(self.workdir))


if __name__ == "__main__":
    unittest.main()
