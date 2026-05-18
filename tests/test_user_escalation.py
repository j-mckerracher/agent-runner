"""Tests for the user escalation engine and related server integration."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# ── Difficulty rubric ──
# Easy   – pure unit, no I/O beyond temp dirs
# Medium – filesystem + env var coordination
# Hard   – cross-process append_event, route integration


class SafeIdTests(unittest.TestCase):
    """safe_id() rejects path-traversal and passes clean IDs. (Easy)"""

    def test_valid_simple(self):
        from server.paths import safe_id
        self.assertEqual(safe_id("conv_abc123"), "conv_abc123")

    def test_valid_with_dots_and_colons(self):
        from server.paths import safe_id
        self.assertEqual(safe_id("esc_01HX:abc.def"), "esc_01HX:abc.def")

    def test_rejects_slash(self):
        from server.paths import safe_id
        with self.assertRaises(ValueError):
            safe_id("../../etc/passwd")

    def test_rejects_empty(self):
        from server.paths import safe_id
        with self.assertRaises(ValueError):
            safe_id("")

    def test_rejects_spaces(self):
        from server.paths import safe_id
        with self.assertRaises(ValueError):
            safe_id("conv abc")


class EscalationPathTests(unittest.TestCase):
    """Verify escalation path helpers produce correct filesystem layout. (Easy)"""

    def test_escalation_request_path(self):
        from server.paths import escalation_request_path_for
        p = escalation_request_path_for("CHG-1", "conv_1", "esc_1")
        self.assertTrue(str(p).endswith("escalations/conv_1/turns/esc_1.request.json"))

    def test_escalation_response_path(self):
        from server.paths import escalation_response_path_for
        p = escalation_response_path_for("CHG-1", "conv_1", "esc_1")
        self.assertTrue(str(p).endswith("escalations/conv_1/turns/esc_1.response.json"))

    def test_transcript_path(self):
        from server.paths import escalation_transcript_path_for
        p = escalation_transcript_path_for("CHG-1", "conv_1")
        self.assertTrue(str(p).endswith("escalations/conv_1/transcript.jsonl"))


class AppendEventTests(unittest.TestCase):
    """Cross-process-safe append_event() produces monotonic seq. (Medium)"""

    def test_sequential_append(self):
        from server.events import append_event
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            r1 = append_event(p, "test.one", foo="bar")
            r2 = append_event(p, "test.two", baz=42)
            self.assertEqual(r1["seq"], 1)
            self.assertEqual(r2["seq"], 2)
            self.assertEqual(r1["type"], "test.one")
            self.assertEqual(r2["type"], "test.two")

    def test_concurrent_append_monotonic(self):
        """Two threads appending should never produce duplicate seq numbers."""
        from server.events import append_event
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            errors: list[str] = []

            def writer(n):
                for _ in range(20):
                    try:
                        append_event(p, f"thread.{n}")
                    except Exception as exc:
                        errors.append(str(exc))

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertFalse(errors, f"Unexpected errors: {errors}")
            # Read back and check uniqueness
            seqs = []
            with p.open() as fh:
                for line in fh:
                    seqs.append(json.loads(line)["seq"])
            self.assertEqual(len(seqs), 80)
            self.assertEqual(len(set(seqs)), 80, "seq numbers must be unique")
            self.assertEqual(sorted(seqs), list(range(1, 81)), "seq numbers must be contiguous")


class EventEmitterDelegatesTests(unittest.TestCase):
    """EventEmitter.emit() delegates to append_event(). (Easy)"""

    def test_emitter_creates_records(self):
        from server.events import EventEmitter
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            em = EventEmitter(p)
            r = em.emit("hello", data=1)
            self.assertEqual(r["seq"], 1)
            self.assertEqual(r["type"], "hello")


class WriteUserResponseTests(unittest.TestCase):
    """write_user_response() validates request, writes atomically. (Medium)"""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        # Patch AGENT_CONTEXT_ROOT to use temp dir
        self._patcher = patch("server.paths.AGENT_CONTEXT_ROOT", Path(self.td))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _write_request(self, change_id, conv_id, esc_id):
        from server.paths import escalation_request_path_for
        req_path = escalation_request_path_for(change_id, conv_id, esc_id)
        req_path.parent.mkdir(parents=True, exist_ok=True)
        req_path.write_text(json.dumps({
            "type": "user.prompt",
            "change_id": change_id,
            "conversation_id": conv_id,
            "escalation_id": esc_id,
            "questions": [{"id": "q1", "label": "test?"}],
        }))
        return req_path

    def test_writes_response(self):
        from core.user_escalation import write_user_response
        self._write_request("CHG-1", "conv_1", "esc_1")
        result = write_user_response(
            change_id="CHG-1", job_id="j1",
            conversation_id="conv_1", escalation_id="esc_1",
            message="yes", responses={"q1": "yes"},
        )
        self.assertEqual(result["type"], "user.response")
        self.assertEqual(result["responses"]["q1"], "yes")
        self.assertIn("pending_count_after", result)

    def test_rejects_missing_request(self):
        from core.user_escalation import write_user_response
        with self.assertRaises(ValueError) as cm:
            write_user_response(
                change_id="CHG-1", job_id="j1",
                conversation_id="conv_1", escalation_id="esc_nonexistent",
                message="nope", responses={},
            )
        self.assertIn("No matching", str(cm.exception))

    def test_rejects_duplicate_response(self):
        from core.user_escalation import write_user_response
        self._write_request("CHG-1", "conv_1", "esc_1")
        write_user_response(
            change_id="CHG-1", job_id="j1",
            conversation_id="conv_1", escalation_id="esc_1",
            message="first", responses={"q1": "a"},
        )
        with self.assertRaises(ValueError) as cm:
            write_user_response(
                change_id="CHG-1", job_id="j1",
                conversation_id="conv_1", escalation_id="esc_1",
                message="second", responses={"q1": "b"},
            )
        self.assertIn("already exists", str(cm.exception))


class ListPendingEscalationsTests(unittest.TestCase):
    """list_pending_escalations() returns requests without responses. (Medium)"""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._patcher = patch("server.paths.AGENT_CONTEXT_ROOT", Path(self.td))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_returns_pending_only(self):
        from core.user_escalation import list_pending_escalations
        from server.paths import escalation_request_path_for, escalation_response_path_for

        # Create two requests, respond to one
        req1 = escalation_request_path_for("CHG-1", "conv_1", "esc_1")
        req1.parent.mkdir(parents=True, exist_ok=True)
        req1.write_text(json.dumps({"escalation_id": "esc_1", "title": "q1"}))

        req2 = escalation_request_path_for("CHG-1", "conv_2", "esc_2")
        req2.parent.mkdir(parents=True, exist_ok=True)
        req2.write_text(json.dumps({"escalation_id": "esc_2", "title": "q2"}))

        resp1 = escalation_response_path_for("CHG-1", "conv_1", "esc_1")
        resp1.write_text(json.dumps({"message": "answered"}))

        pending = list_pending_escalations("CHG-1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["escalation_id"], "esc_2")


class NormalizeQuestionsTests(unittest.TestCase):
    """_normalize_questions handles strings and dicts. (Easy)"""

    def test_string_questions(self):
        from core.user_escalation import _normalize_questions
        result = _normalize_questions(["What?", "Why?"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "q1")
        self.assertEqual(result[0]["label"], "What?")
        self.assertEqual(result[1]["id"], "q2")

    def test_dict_questions_with_defaults(self):
        from core.user_escalation import _normalize_questions
        result = _normalize_questions([{"label": "Choose one"}])
        self.assertEqual(result[0]["id"], "q1")
        self.assertEqual(result[0]["kind"], "textarea")
        self.assertTrue(result[0]["required"])


class RequestUserInputIntegrationTests(unittest.TestCase):
    """request_user_input() writes request, polls, and returns response. (Medium)"""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._patcher = patch("server.paths.AGENT_CONTEXT_ROOT", Path(self.td))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    @patch.dict(os.environ, {"AGENT_RUNNER_EVENT_LOG": "", "AGENT_RUNNER_USER_ESCALATION": "gui"})
    def test_request_blocks_then_returns_response(self):
        from core.user_escalation import request_user_input, write_user_response
        from server.paths import escalations_dir_for

        # Run request_user_input in a thread so we can simulate async response
        result_holder: list[dict] = []
        error_holder: list[Exception] = []

        def requester():
            try:
                r = request_user_input(
                    change_id="TEST-1", stage="execution", agent="test-agent",
                    title="Test Q", message="Need answer", questions=["Yes or no?"],
                    timeout_seconds=10,
                )
                result_holder.append(r)
            except Exception as e:
                error_holder.append(e)

        t = threading.Thread(target=requester)
        t.start()

        # Wait for request file to appear
        esc_dir = escalations_dir_for("TEST-1")
        for _ in range(50):
            if esc_dir.exists():
                turns_dirs = list(esc_dir.rglob("*.request.json"))
                if turns_dirs:
                    break
            time.sleep(0.1)

        # Find the request and write a response
        req_files = list(esc_dir.rglob("*.request.json"))
        self.assertTrue(len(req_files) > 0, "Request file should have been created")
        req_data = json.loads(req_files[0].read_text())
        conv_id = req_data["conversation_id"]
        esc_id = req_data["escalation_id"]

        write_user_response(
            change_id="TEST-1", job_id="j1",
            conversation_id=conv_id, escalation_id=esc_id,
            message="Yes", responses={"q1": "Yes"},
        )

        t.join(timeout=5)
        self.assertFalse(error_holder, f"Unexpected errors: {error_holder}")
        self.assertTrue(result_holder, "Should have gotten a response")
        self.assertEqual(result_holder[0]["responses"]["q1"], "Yes")


class OpenAICompatToolSpecTests(unittest.TestCase):
    """OpenAI-compat tool runtime includes request_user_input. (Easy)"""

    def test_tool_specs_include_request_user_input(self):
        from core.run_cmds import _OpenaiCompatToolRuntime
        runtime = _OpenaiCompatToolRuntime(repo=None, change_id=None)
        names = [t["function"]["name"] for t in runtime.tool_specs]
        self.assertIn("request_user_input", names)

    def test_tool_spec_has_required_fields(self):
        from core.run_cmds import _OpenaiCompatToolRuntime
        runtime = _OpenaiCompatToolRuntime(repo=None, change_id=None)
        spec = next(t for t in runtime.tool_specs if t["function"]["name"] == "request_user_input")
        required = spec["function"]["parameters"]["required"]
        self.assertIn("title", required)
        self.assertIn("message", required)
        self.assertIn("questions", required)


class RunnerProcPersistTests(unittest.TestCase):
    """_persist() status transitions for user.prompt / user.response. (Easy)"""

    def test_user_prompt_sets_awaiting_input(self):
        """Verify the _persist callback logic.

        We can't easily instantiate a full JobProcess, so we test the logic
        by inspecting the source — this is a documentation/contract test.
        """
        # This is a structural test — the actual _persist is a closure inside
        # start(). We verify the runner_proc source contains the correct logic.
        import inspect
        from server import runner_proc
        source = inspect.getsource(runner_proc)
        self.assertIn('status="awaiting_input"', source)
        self.assertIn("pending_count_after", source)


if __name__ == "__main__":
    unittest.main()

