"""Tests for event emitter, tailer, and SSE resume."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from server.events import EventEmitter, EventTailer, read_events_from_file


class EventEmitterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_easy__emitter_writes_jsonl_to_file(self):
        emitter = EventEmitter(self.path)
        emitter.emit({"type": "test.event", "value": 42})
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        evt = json.loads(lines[0])
        self.assertEqual(evt["type"], "test.event")
        self.assertEqual(evt["value"], 42)

    def test_easy__emitter_noop_when_path_is_none(self):
        emitter = EventEmitter(None)
        # Should not raise
        emitter.emit({"type": "test"})
        emitter.stage_start("intake")

    def test_medium__emitter_assigns_monotonic_seq(self):
        emitter = EventEmitter(self.path)
        for i in range(5):
            emitter.emit({"type": "t", "i": i})
        events = read_events_from_file(self.path)
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, list(range(1, 6)))

    def test_medium__emitter_adds_ts_field(self):
        emitter = EventEmitter(self.path)
        emitter.emit({"type": "t"})
        evt = json.loads(self.path.read_text().splitlines()[0])
        self.assertIn("ts", evt)

    def test_medium__stage_start_emits_correct_type(self):
        emitter = EventEmitter(self.path)
        emitter.stage_start("intake")
        evt = read_events_from_file(self.path)[0]
        self.assertEqual(evt["type"], "stage.start")
        self.assertEqual(evt["stage"], "intake")

    def test_medium__stage_end_emits_correct_type(self):
        emitter = EventEmitter(self.path)
        emitter.stage_end("intake", status="ok")
        evt = read_events_from_file(self.path)[0]
        self.assertEqual(evt["type"], "stage.end")
        self.assertEqual(evt["status"], "ok")

    def test_medium__job_end_emits_correct_fields(self):
        emitter = EventEmitter(self.path)
        emitter.job_end(status="succeeded", exit_code=0)
        evt = read_events_from_file(self.path)[0]
        self.assertEqual(evt["type"], "job.end")
        self.assertEqual(evt["status"], "succeeded")
        self.assertEqual(evt["exit_code"], 0)

    def test_medium__metrics_event_has_correct_fields(self):
        emitter = EventEmitter(self.path)
        emitter.metrics(tokens_in=100, tokens_out=50, cost_usd=0.005)
        evt = read_events_from_file(self.path)[0]
        self.assertEqual(evt["type"], "metrics")
        self.assertEqual(evt["tokens_in"], 100)
        self.assertEqual(evt["tokens_out"], 50)
        self.assertAlmostEqual(evt["cost_usd"], 0.005)


class ReadEventsFromFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_easy__returns_empty_list_for_empty_file(self):
        result = read_events_from_file(self.path)
        self.assertEqual(result, [])

    def test_easy__returns_empty_list_for_nonexistent_file(self):
        result = read_events_from_file("/tmp/nonexistent_xyz_123.jsonl")
        self.assertEqual(result, [])

    def test_medium__skips_malformed_lines(self):
        self.path.write_text('{"type":"ok"}\nNOT JSON\n{"type":"also_ok"}\n')
        events = read_events_from_file(self.path)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "ok")
        self.assertEqual(events[1]["type"], "also_ok")


class EventTailerTests(unittest.IsolatedAsyncioTestCase):
    async def test_medium__tailer_replays_existing_events(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            path = f.name
            # Write events before tailer starts
            for i in range(3):
                f.write(json.dumps({"type": "t", "seq": i+1}) + "\n")

        tailer = EventTailer(path)
        q = await tailer.subscribe()
        task = asyncio.create_task(tailer.run())

        collected = []
        for _ in range(3):
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt is None:
                break
            collected.append(evt)

        # Stop tailer (write job.end)
        with open(path, "a") as f:
            f.write(json.dumps({"type": "job.end", "seq": 4}) + "\n")

        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            tailer.stop()

        os.unlink(path)
        self.assertEqual(len(collected), 3)

    async def test_medium__tailer_broadcasts_to_multiple_subscribers(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            path = f.name

        tailer = EventTailer(path)
        q1 = await tailer.subscribe()
        q2 = await tailer.subscribe()
        task = asyncio.create_task(tailer.run())

        # Write a single event + job.end
        with open(path, "a") as f:
            f.write(json.dumps({"type": "t", "seq": 1}) + "\n")
            f.write(json.dumps({"type": "job.end", "seq": 2}) + "\n")

        async def drain(q):
            events = []
            while True:
                evt = await asyncio.wait_for(q.get(), timeout=3.0)
                if evt is None:
                    break
                events.append(evt)
                if evt.get("type") == "job.end":
                    break
            return events

        r1, r2 = await asyncio.gather(drain(q1), drain(q2))
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            tailer.stop()

        os.unlink(path)
        self.assertGreaterEqual(len(r1), 1)
        self.assertGreaterEqual(len(r2), 1)

    async def test_medium__tailer_resume_from_offset_via_read_events(self):
        """Verify that read_events_from_file + offset slicing works for resume."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            path = f.name
            for i in range(5):
                f.write(json.dumps({"type": "t", "seq": i+1}) + "\n")

        all_events = read_events_from_file(path)
        # Simulate resume after seq 3
        resume_seq = 3
        resumed = [e for e in all_events if e.get("seq", 0) > resume_seq]
        os.unlink(path)

        self.assertEqual(len(resumed), 2)
        self.assertEqual(resumed[0]["seq"], 4)
        self.assertEqual(resumed[1]["seq"], 5)


if __name__ == "__main__":
    unittest.main()
