"""Tests for the omp RPC-mode client (core/omp_rpc.py).

A fake omp subprocess replays the frame sequences confirmed against a live omp
install during the design spikes.
"""

import json
import unittest
from unittest.mock import patch

from core.omp_rpc import (
    INTERACTIVE_UI_METHODS,
    OmpRpcError,
    OmpRpcSession,
    _extract_text_from_messages,
    _tool_result_text,
)


class _FakeStdin:
    def __init__(self, capture):
        self.capture = capture
        self.closed = False

    def write(self, data):
        self.capture.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakePopen:
    """Minimal stand-in for subprocess.Popen driving the RPC reader."""

    def __init__(self, lines, returncode=0, stderr_lines=None, capture=None, **kwargs):
        self.stdout = iter([line + "\n" for line in lines])
        self.stderr = iter([(s + "\n") for s in (stderr_lines or [])])
        self.stdin = _FakeStdin(capture if capture is not None else [])
        self._final_returncode = returncode
        self.returncode = None
        self.pid = 4321

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self._final_returncode
        return self.returncode

    def kill(self):
        self.returncode = self._final_returncode


def _fake_popen_factory(lines, capture, returncode=0, stderr_lines=None):
    def _factory(cmd, **kwargs):
        return _FakePopen(
            lines,
            returncode=returncode,
            stderr_lines=stderr_lines,
            capture=capture,
            **kwargs,
        )

    return _factory


def _run(lines, *, capture=None, returncode=0, stderr_lines=None, **session_kwargs):
    capture = capture if capture is not None else []
    with patch(
        "core.omp_rpc.subprocess.Popen",
        _fake_popen_factory(lines, capture, returncode, stderr_lines),
    ):
        session = OmpRpcSession(repo="/tmp/repo", **session_kwargs)
        session.start()
        try:
            result = session.run_prompt("do the thing")
        finally:
            session.close()
    return result, capture


class BuildCmdTests(unittest.TestCase):
    def test_easy__default_flags_headless_no_model(self):
        cmd = OmpRpcSession(repo="/tmp/repo").build_cmd()
        self.assertEqual(cmd[0], "omp")
        self.assertIn("--mode", cmd)
        self.assertEqual(cmd[cmd.index("--mode") + 1], "rpc")
        self.assertIn("--no-session", cmd)
        self.assertIn("--approval-mode", cmd)
        self.assertEqual(cmd[cmd.index("--approval-mode") + 1], "yolo")
        self.assertIn("--no-extensions", cmd)
        self.assertNotIn("--model", cmd)

    def test_easy__model_included_when_specified(self):
        cmd = OmpRpcSession(repo="/tmp/repo", model="glm-5.2").build_cmd()
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "glm-5.2")

    def test_easy__no_extensions_can_be_disabled(self):
        cmd = OmpRpcSession(repo="/tmp/repo", no_extensions=False).build_cmd()
        self.assertNotIn("--no-extensions", cmd)


class RunPromptTests(unittest.TestCase):
    def test_medium__assembles_text_and_tool_calls_across_turns(self):
        events = []
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "extension_ui_request", "id": "w1", "method": "setWidget", "widgetKey": "x"}),
            json.dumps({"type": "response", "command": "prompt", "success": True}),
            json.dumps({"type": "agent_start"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({
                "type": "tool_execution_start",
                "toolCallId": "ollama:0:bash",
                "toolName": "bash",
                "args": {"command": "echo hi"},
                "intent": "Run echo hi",
            }),
            json.dumps({
                "type": "tool_execution_end",
                "toolCallId": "ollama:0:bash",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "hi\n"}]},
                "isError": False,
            }),
            json.dumps({"type": "turn_end", "toolResults": []}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "message_start"}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_start"}}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "done"}}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_end", "content": "done"}}),
            json.dumps({"type": "message_end"}),
            json.dumps({
                "type": "agent_end",
                "messages": [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            }),
        ]
        result, _ = _run(lines, on_event=lambda t, p: events.append((t, p)))

        self.assertEqual(result.text, "done")
        self.assertEqual(result.turns, 2)
        self.assertEqual(len(result.tool_calls), 1)
        tc = result.tool_calls[0]
        self.assertEqual(tc.tool_name, "bash")
        self.assertEqual(tc.tool_call_id, "ollama:0:bash")
        self.assertFalse(tc.is_error)
        self.assertEqual(tc.result_text, "hi\n")
        self.assertIn("omp.tool_start", [t for t, _ in events])
        self.assertIn("omp.tool_end", [t for t, _ in events])

    def test_medium__final_text_falls_back_to_deltas_when_messages_absent(self):
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hel"}}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "lo"}}),
            json.dumps({"type": "agent_end"}),
        ]
        result, _ = _run(lines)
        self.assertEqual(result.text, "hello")

    def test_medium__setwidget_ui_request_is_not_answered(self):
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "extension_ui_request", "id": "w1", "method": "setWidget", "widgetKey": "x"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "ok"}}),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
        _, capture = _run(lines)
        # Only the prompt command should have been written — no ui response.
        sent = [json.loads(c) for c in capture]
        self.assertTrue(any(m.get("type") == "prompt" for m in sent))
        self.assertFalse(any(m.get("type") == "extension_ui_response" for m in sent))

    def test_medium__interactive_ui_request_is_answered_via_handler(self):
        seen = {}

        def handler(frame):
            seen["method"] = frame.get("method")
            return "confirmed"

        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "extension_ui_request", "id": "u9", "method": "open_url", "url": "https://x"}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "ok"}}),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
        _, capture = _run(lines, ui_request_handler=handler)
        sent = [json.loads(c) for c in capture]
        responses = [m for m in sent if m.get("type") == "extension_ui_response"]
        self.assertEqual(seen["method"], "open_url")
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], "u9")
        self.assertEqual(responses[0]["value"], "confirmed")

    def test_medium__interactive_ui_request_defaults_to_cancel_without_handler(self):
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "extension_ui_request", "id": "u9", "method": "confirm"}),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
        _, capture = _run(lines)
        sent = [json.loads(c) for c in capture]
        responses = [m for m in sent if m.get("type") == "extension_ui_response"]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["value"], "cancelled")

    def test_medium__eof_before_agent_end_raises(self):
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "response", "command": "prompt", "success": True}),
        ]
        with self.assertRaises(OmpRpcError):
            _run(lines, stderr_lines=["boom: provider error"])

    def test_medium__failed_prompt_response_raises_without_agent_end(self):
        # Reproduces the live glm-5.2 hang: omp accepts the command (success
        # ack) then rejects it (no API key) and never emits agent_end. Without
        # handling the failure response the client blocks until turn_timeout.
        err = (
            "No API key found for zai.\n\nUse /login, set an API key environment "
            "variable, or create ~/.omp/agent/agent.db"
        )
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "extension_ui_request", "id": "w1", "method": "setWidget", "widgetKey": "x"}),
            json.dumps({"type": "response", "command": "prompt", "success": True}),
            json.dumps({"type": "response", "command": "prompt", "success": False, "error": err}),
        ]
        with self.assertRaises(OmpRpcError) as ctx:
            _run(lines)
        self.assertIn("No API key found for zai", str(ctx.exception))

    def test_medium__success_response_ack_does_not_end_turn(self):
        # A success:true response is only a command ack; the turn still ends on
        # agent_end and its text must be returned normally.
        lines = [
            json.dumps({"type": "ready"}),
            json.dumps({"type": "response", "command": "prompt", "success": True}),
            json.dumps({"type": "turn_start"}),
            json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}}),
            json.dumps({"type": "agent_end", "messages": [{"role": "assistant", "content": "hi"}]}),
        ]
        result, _ = _run(lines)
        self.assertEqual(result.text, "hi")


class HelperTests(unittest.TestCase):
    def test_easy__extract_text_prefers_last_assistant_message(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "first"}]},
            {"role": "assistant", "content": "second"},
        ]
        self.assertEqual(_extract_text_from_messages(messages), "second")

    def test_easy__extract_text_handles_missing(self):
        self.assertEqual(_extract_text_from_messages(None), "")
        self.assertEqual(_extract_text_from_messages([{"role": "user", "content": "x"}]), "")

    def test_easy__tool_result_text_flattens_content(self):
        result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        self.assertEqual(_tool_result_text(result), "ab")
        self.assertIsNone(_tool_result_text(None))

    def test_easy__interactive_methods_exclude_status_pushes(self):
        self.assertIn("open_url", INTERACTIVE_UI_METHODS)
        self.assertNotIn("setStatus", INTERACTIVE_UI_METHODS)
        self.assertNotIn("setWidget", INTERACTIVE_UI_METHODS)


if __name__ == "__main__":
    unittest.main()
