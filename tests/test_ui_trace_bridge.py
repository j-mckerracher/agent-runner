from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _fake_span(*_args, **_kwargs):
    class _Span:
        input = None
        output = None

    yield _Span()


def _fake_track(**_kwargs):
    def decorator(func):
        return func

    return decorator


class UiTraceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        os.environ["AGENT_RUNNER_EVENT_LOG"] = self.tmp.name
        from server import events
        from core.ui_trace_bridge import set_opik_tracing_enabled

        events._default = None
        set_opik_tracing_enabled(False)

    def tearDown(self):
        os.environ.pop("AGENT_RUNNER_EVENT_LOG", None)
        from server import events
        from core.ui_trace_bridge import set_opik_tracing_enabled

        events._default = None
        set_opik_tracing_enabled(False)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_medium__track_with_ui_emits_opik_trace_start_and_end(self):
        from server.events import read_all
        from core.ui_trace_bridge import set_opik_tracing_enabled, track_with_ui

        with patch("core.ui_trace_bridge.opik.track", side_effect=_fake_track):
            set_opik_tracing_enabled(True)

            @track_with_ui(
                name="stage:intake",
                type="tool",
                metadata_getter=lambda change_id, runner="claude": {"change_id": change_id, "runner": runner},
            )
            def sample(change_id: str, runner: str = "claude") -> str:
                return f"{change_id}:{runner}"

            result = sample("WI-123")

        self.assertEqual(result, "WI-123:claude")
        events = read_all(self.tmp.name)
        self.assertEqual([event["type"] for event in events], ["opik.start", "opik.end"])
        self.assertEqual(events[0]["name"], "stage:intake")
        self.assertEqual(events[0]["kind"], "trace")
        self.assertEqual(events[0]["metadata"]["change_id"], "WI-123")
        self.assertEqual(events[1]["status"], "ok")

    def test_medium__track_with_ui_marks_error_when_wrapped_call_raises(self):
        from server.events import read_all
        from core.ui_trace_bridge import set_opik_tracing_enabled, track_with_ui

        with patch("core.ui_trace_bridge.opik.track", side_effect=_fake_track):
            set_opik_tracing_enabled(True)

            @track_with_ui(name="sdk-evaluator", type="llm")
            def explode() -> None:
                raise ValueError("boom")

            with self.assertRaisesRegex(ValueError, "boom"):
                explode()

        events = read_all(self.tmp.name)
        self.assertEqual(events[-1]["type"], "opik.end")
        self.assertEqual(events[-1]["status"], "error")
        self.assertIn("ValueError: boom", events[-1]["error"])

    def test_medium__start_span_with_ui_tracks_nested_depth(self):
        from server.events import read_all
        from core.ui_trace_bridge import set_opik_tracing_enabled, start_span_with_ui, track_with_ui

        with (
            patch("core.ui_trace_bridge.opik.track", side_effect=_fake_track),
            patch("core.ui_trace_bridge.opik.start_as_current_span", side_effect=_fake_span),
        ):
            set_opik_tracing_enabled(True)

            @track_with_ui(name="loop:uow-eval", type="general")
            def run_loop() -> None:
                with start_span_with_ui(
                    "uow-iteration-1",
                    type="general",
                    metadata={"iteration": 1},
                ) as span:
                    span.output = {"passed": True}

            run_loop()

        events = read_all(self.tmp.name)
        self.assertEqual(
            [(event["type"], event["name"], event["depth"]) for event in events],
            [
                ("opik.start", "loop:uow-eval", 0),
                ("opik.start", "uow-iteration-1", 1),
                ("opik.end", "uow-iteration-1", 1),
                ("opik.end", "loop:uow-eval", 0),
            ],
        )
        self.assertEqual(events[1]["kind"], "span")

    def test_medium__disabled_track_with_ui_skips_opik_sdk_calls(self):
        from server.events import read_all
        from core.ui_trace_bridge import track_with_ui

        with patch("core.ui_trace_bridge.opik.track") as opik_track:
            @track_with_ui(name="stage:qa", type="tool")
            def sample() -> str:
                return "ok"

            result = sample()

        self.assertEqual(result, "ok")
        opik_track.assert_not_called()
        events = read_all(self.tmp.name)
        self.assertEqual([event["type"] for event in events], ["opik.start", "opik.end"])

    def test_medium__disabled_start_span_with_ui_returns_passive_span(self):
        from server.events import read_all
        from core.ui_trace_bridge import start_span_with_ui

        with patch("core.ui_trace_bridge.opik.start_as_current_span") as start_span:
            with start_span_with_ui("uow-iteration-1", type="general") as span:
                span.input = {"iteration": 1}
                span.output = {"passed": True}

        start_span.assert_not_called()
        events = read_all(self.tmp.name)
        self.assertEqual([event["type"] for event in events], ["opik.start", "opik.end"])


if __name__ == "__main__":
    unittest.main()
