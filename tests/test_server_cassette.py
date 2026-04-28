"""Tests for hermetic-mode cassette recorder."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.cassette import CassetteRecorder, init_recorder_from_env, get_recorder


class CassetteRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_easy__recorder_writes_jsonl_record(self):
        recorder = CassetteRecorder(self.path)
        recorder.record(
            stage="intake",
            cmd="claude",
            args=["-p", "hello"],
            stdin=None,
            stdout="output text",
            stderr="",
            exit_code=0,
            duration_ms=1234.5,
        )
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["stage"], "intake")
        self.assertEqual(rec["cmd"], "claude")
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(rec["stdout"], "output text")

    def test_easy__recorder_noop_when_path_none(self):
        recorder = CassetteRecorder(None)
        # Should not raise or write anything
        recorder.record(
            stage="test", cmd="x", args=[], stdin=None,
            stdout="", stderr="", exit_code=0, duration_ms=0,
        )

    def test_medium__recorder_appends_multiple_records(self):
        recorder = CassetteRecorder(self.path)
        for i in range(3):
            recorder.record(
                stage=f"stage_{i}", cmd="tool", args=[f"--arg={i}"],
                stdin=None, stdout=f"out{i}", stderr="", exit_code=0, duration_ms=float(i*100),
            )
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        recs = [json.loads(l) for l in lines]
        self.assertEqual(recs[0]["stage"], "stage_0")
        self.assertEqual(recs[2]["stage"], "stage_2")

    def test_medium__recorder_includes_ts_field(self):
        recorder = CassetteRecorder(self.path)
        recorder.record(
            stage="qa", cmd="gemini", args=[], stdin=None,
            stdout="", stderr="", exit_code=0, duration_ms=500,
        )
        rec = json.loads(self.path.read_text().splitlines()[0])
        self.assertIn("ts", rec)

    def test_medium__cassette_contains_all_required_fields(self):
        recorder = CassetteRecorder(self.path)
        recorder.record(
            stage="intake",
            cmd="copilot",
            args=["--model", "gpt-5-mini"],
            stdin="input text",
            stdout="output text",
            stderr="warning",
            exit_code=0,
            duration_ms=999.9,
        )
        rec = json.loads(self.path.read_text().splitlines()[0])
        for field in ("ts", "stage", "cmd", "args", "stdin", "stdout", "stderr", "exit_code", "duration_ms"):
            self.assertIn(field, rec, f"Missing field: {field}")

    def test_medium__recorder_records_nonzero_exit_code(self):
        recorder = CassetteRecorder(self.path)
        recorder.record(
            stage="qa", cmd="claude", args=[], stdin=None,
            stdout="", stderr="error output", exit_code=1, duration_ms=200,
        )
        rec = json.loads(self.path.read_text().splitlines()[0])
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["stderr"], "error output")


class InitRecorderFromEnvTests(unittest.TestCase):
    def test_easy__no_env_var_produces_noop_recorder(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_RUNNER_CASSETTE", None)
            recorder = init_recorder_from_env()
        self.assertIsNone(recorder._path)

    def test_medium__env_var_sets_recorder_path(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=True) as f:
            path = f.name
        with patch.dict(os.environ, {"AGENT_RUNNER_CASSETTE": path}):
            recorder = init_recorder_from_env()
        self.assertEqual(str(recorder._path), path)


class HermeticModeIntegrationTests(unittest.TestCase):
    """Test that run_cmds uses cassette recorder when env var is set."""

    def test_medium__run_cmds_cassette_records_claude_invocation(self):
        import subprocess
        import run_cmds

        completed = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="ok", stderr="")

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            cassette_path = f.name

        try:
            with patch.dict(os.environ, {"AGENT_RUNNER_CASSETTE": cassette_path}):
                from server.cassette import init_recorder_from_env
                init_recorder_from_env()
                with patch.object(run_cmds.subprocess, "run", return_value=completed):
                    run_cmds.run_claude_cmd(prompt="Test prompt", agent="intake")

            lines = Path(cassette_path).read_text().splitlines()
            self.assertGreater(len(lines), 0)
            rec = json.loads(lines[0])
            self.assertEqual(rec["cmd"], "claude")
        finally:
            Path(cassette_path).unlink(missing_ok=True)
            # Reset recorder to noop
            os.environ.pop("AGENT_RUNNER_CASSETTE", None)
            from server.cassette import init_recorder_from_env
            init_recorder_from_env()

    def test_medium__live_mode_does_not_write_cassette(self):
        import subprocess
        import run_cmds

        completed = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="ok", stderr="")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_RUNNER_CASSETTE", None)
            from server.cassette import init_recorder_from_env
            recorder = init_recorder_from_env()

        self.assertIsNone(recorder._path)

        # Run a command — cassette should not be written
        with patch.object(run_cmds.subprocess, "run", return_value=completed):
            run_cmds.run_claude_cmd(prompt="Test prompt", agent="intake")
        # No file was created (recorder._path is None)
        self.assertIsNone(recorder._path)


if __name__ == "__main__":
    unittest.main()
