"""Tests for the JSONL event emitter and cassette recorder."""
from __future__ import annotations

import json
import os
import tempfile
import unittest


class EventEmitterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        os.environ["AGENT_RUNNER_EVENT_LOG"] = self.tmp.name
        # Force a fresh emitter per test.
        from server import events
        events._EMITTER = None  # type: ignore[attr-defined]

    def tearDown(self):
        os.environ.pop("AGENT_RUNNER_EVENT_LOG", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_easy__emit_writes_jsonl_with_monotonic_seq(self):
        from server.events import emit, read_all
        # Truncate any preamble so seq starts at 1.
        open(self.tmp.name, "w").close()
        from server import events
        events._EMITTER = None  # type: ignore[attr-defined]
        emit("stage.start", stage="intake")
        emit("log", level="info", msg="hello")
        emit("stage.end", stage="intake", status="ok")
        evs = read_all(self.tmp.name)
        self.assertEqual([e["type"] for e in evs], ["stage.start", "log", "stage.end"])
        self.assertEqual([e["seq"] for e in evs], [1, 2, 3])
        for e in evs:
            self.assertIn("ts", e)

    def test_medium__emit_no_op_when_env_unset(self):
        os.environ.pop("AGENT_RUNNER_EVENT_LOG", None)
        from server import events
        events._EMITTER = None  # type: ignore[attr-defined]
        from server.events import emit
        # Should not raise.
        emit("anything", x=1)


class CassetteTests(unittest.TestCase):
    def test_easy__record_no_op_when_env_unset(self):
        os.environ.pop("AGENT_RUNNER_CASSETTE", None)
        from server.cassette import record
        record(cmd=["x"], stdin=None, stdout="", stderr="", exit_code=0, duration_ms=0)

    def test_medium__record_writes_jsonl_when_env_set(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            os.environ["AGENT_RUNNER_CASSETTE"] = path
            from server.cassette import record
            record(cmd=["echo", "hi"], stdin=None, stdout="hi\n", stderr="", exit_code=0, duration_ms=12)
            record(cmd=["false"], stdin=None, stdout="", stderr="", exit_code=1, duration_ms=3)
            with open(path) as fp:
                lines = [json.loads(l) for l in fp if l.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["cmd"], ["echo", "hi"])
            self.assertEqual(lines[1]["exit_code"], 1)
        finally:
            os.environ.pop("AGENT_RUNNER_CASSETTE", None)
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
