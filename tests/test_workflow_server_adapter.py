"""Prompt 10 characterization tests: server jobs reach the workflow CLI adapter.

`JobProcess._build_cmd` already targets `run.py` (regular jobs) or
`eval/runner.py` (benchmark_evaluation jobs) via CLI flags -- it never
re-implements `run.py::main`'s internal argument mapping. These tests pin
that routing and prove regular jobs invoke the exact `run.py` whose
`__main__` now routes through `run.run_spec_from_args` /
`run.execute_cli_args` / `WorkflowRunner`, while process-group /
cancellation isolation is untouched.

No real subprocess is spawned in these tests except where noted; process
launch is asserted via patched `subprocess.Popen`.
"""

from __future__ import annotations

import os
import signal
import unittest
from unittest.mock import MagicMock, patch

import run as run_module
from server.events import EventBus
from server.paths import RUNNER_ROOT
from server.runner_proc import JobProcess


class ServerRegularJobReachesWorkflowCliAdapterTests(unittest.TestCase):
    def test_regular_job_targets_run_py(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "WI-1",
            "runner": "claude",
            "events_path": "/tmp/events.jsonl",
        }
        cmd = JobProcess(job, EventBus(), None)._build_cmd()

        self.assertEqual(cmd[1], str(RUNNER_ROOT / "run.py"))
        self.assertTrue(hasattr(run_module, "run_spec_from_args"))
        self.assertTrue(hasattr(run_module, "execute_cli_args"))

    def test_regular_job_preserves_runner_model_and_story_source(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "WI-2",
            "runner": "codex",
            "model": "gpt-5.5",
            "story_file": "/tmp/story.json",
            "events_path": "/tmp/events.jsonl",
        }
        cmd = JobProcess(job, EventBus(), None)._build_cmd()

        self.assertIn("--story-file", cmd)
        self.assertEqual(cmd[cmd.index("--story-file") + 1], "/tmp/story.json")
        self.assertIn("--runner", cmd)
        self.assertEqual(cmd[cmd.index("--runner") + 1], "codex")
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-5.5")

    def test_regular_job_preserves_agent_overrides_extra_context_and_log_level(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "WI-3",
            "runner": "claude",
            "agent_llm_overrides": '{"qa-engineer":{"runner":"codex","model":"gpt-5.5"}}',
            "extra_context": "see PR #9",
            "log_level": "debug",
            "events_path": "/tmp/events.jsonl",
        }
        cmd = JobProcess(job, EventBus(), None)._build_cmd()

        self.assertIn("qa-engineer=codex", cmd)
        self.assertIn("qa-engineer=gpt-5.5", cmd)
        self.assertIn("--extra-context", cmd)
        self.assertEqual(cmd[cmd.index("--extra-context") + 1], "see PR #9")
        self.assertIn("--log-level", cmd)
        self.assertEqual(cmd[cmd.index("--log-level") + 1], "debug")


class ServerBenchmarkJobStillInvokesEvalCliTests(unittest.TestCase):
    def test_benchmark_job_targets_eval_runner_not_run_py(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "eval-benchmark",
            "runner": "claude",
            "run_kind": "benchmark_evaluation",
            "eval_runner_args": '{"repo": "/tmp/repo", "sha": "abc123", "runner": "claude"}',
            "events_path": "/tmp/events.jsonl",
        }
        cmd = JobProcess(job, EventBus(), None)._build_cmd()

        self.assertEqual(cmd[1], str(RUNNER_ROOT / "eval" / "runner.py"))
        self.assertNotIn(str(RUNNER_ROOT / "run.py"), cmd)


class ServerPreservesSubprocessAndProcessGroupIsolationTests(unittest.TestCase):
    def test_start_uses_setsid_process_group(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "WI-4",
            "runner": "claude",
            "events_path": "/tmp/events.jsonl",
        }
        proc = JobProcess(job, EventBus(), None)

        with patch("server.runner_proc.clean_change_workspace"), patch(
            "server.runner_proc.Path.write_text"
        ), patch("server.runner_proc.FileTailer"), patch(
            "server.runner_proc.db.update_job"
        ), patch("server.runner_proc.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=4242)
            proc.start()

        _, kwargs = mock_popen.call_args
        if os.name != "nt":
            self.assertEqual(kwargs.get("preexec_fn"), os.setsid)

    def test_cancel_kills_process_group_not_single_process(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "WI-5",
            "runner": "claude",
            "events_path": "/tmp/events.jsonl",
        }
        proc = JobProcess(job, EventBus(), None)
        fake_proc = MagicMock()
        fake_proc.pid = 4242
        fake_proc.poll.return_value = None
        proc.proc = fake_proc

        with patch("server.runner_proc.os.getpgid", return_value=4242) as mock_getpgid, patch(
            "server.runner_proc.os.killpg"
        ) as mock_killpg:
            proc.cancel()

        if os.name != "nt":
            mock_getpgid.assert_called_once_with(4242)
            mock_killpg.assert_called_once_with(4242, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
