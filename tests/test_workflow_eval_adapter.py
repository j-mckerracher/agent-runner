"""Prompt 10 characterization tests: eval reaches the workflow CLI adapter.

`eval.runner.build_workflow_command` already targets `run.py` via CLI
flags -- it never re-implements `run.py::main`'s internal argument
mapping. These tests pin that command shape and prove the executable it
invokes is the same `run.py` whose `__main__` now routes through
`run.run_spec_from_args` / `run.execute_cli_args` / `WorkflowRunner`.

No real LLM calls, no network, no real subprocess execution of the
workflow itself -- `subprocess.Popen` is patched where process-launch
behavior is asserted.
"""

from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import run as run_module
from eval import runner as eval_runner


class EvalReachesWorkflowCliAdapterTests(unittest.TestCase):
    def test_build_workflow_command_targets_run_py(self):
        args = Namespace(
            runner="claude",
            model=None,
            log_level="warning",
            include_lessons=False,
            workflow_timeout=900,
        )
        cmd = eval_runner.build_workflow_command(Path("/tmp/bench"), Path("/tmp/workspace"), args)

        # The eval subprocess targets the exact same run.py module whose
        # __main__ now calls execute_cli_args -> RunSpec -> WorkflowRunner.
        self.assertEqual(cmd[1], str(eval_runner.RUN_PY))
        self.assertIn("--repo", cmd)
        self.assertIn(str(Path("/tmp/workspace")), cmd)
        self.assertIn("--story-file", cmd)
        self.assertIn("--runner", cmd)
        self.assertIn("claude", cmd)
        self.assertIn("--headless", cmd)
        self.assertIn("--log-level", cmd)
        self.assertIn("--skip-lessons-optimizer", cmd)

    def test_build_workflow_command_includes_model_when_present(self):
        args = Namespace(
            runner="claude",
            model="claude-sonnet-4-6",
            log_level="warning",
            include_lessons=False,
            workflow_timeout=900,
        )
        cmd = eval_runner.build_workflow_command(Path("/tmp/bench"), Path("/tmp/workspace"), args)
        self.assertIn("--model", cmd)
        self.assertIn("claude-sonnet-4-6", cmd)

    def test_run_py_target_is_the_adapted_executable(self):
        # eval/runner.py's RUN_PY constant must resolve to the same run.py
        # file that owns run_spec_from_args/execute_cli_args -- eval never
        # gets its own copy of the CLI-to-RunSpec mapping.
        self.assertEqual(Path(eval_runner.RUN_PY).resolve(), Path(run_module.__file__).resolve())
        self.assertTrue(hasattr(run_module, "run_spec_from_args"))
        self.assertTrue(hasattr(run_module, "execute_cli_args"))


class EvalPreservesSubprocessIsolationTests(unittest.TestCase):
    def test_run_workflow_with_progress_uses_subprocess_popen_not_inprocess_runner(self):
        # Eval must remain subprocess-based: it must launch the workflow via
        # subprocess.Popen rather than importing and calling WorkflowRunner
        # in-process (no such import exists in eval.runner at all).
        fake_proc = MagicMock()
        fake_proc.poll.side_effect = [None, 0, 0, 0]
        fake_proc.returncode = 0

        self.assertNotIn("WorkflowRunner", dir(eval_runner))

        with patch("eval.runner.subprocess.Popen", return_value=fake_proc) as mock_popen, patch(
            "eval.runner._poll_workflow_events", return_value=False
        ), patch("eval.runner.time.sleep"):
            eval_runner.run_workflow_with_progress(
                ["python", "run.py", "--headless"],
                cwd=Path("/tmp"),
                timeout=5,
                env={},
                include_lessons=False,
            )

        mock_popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
