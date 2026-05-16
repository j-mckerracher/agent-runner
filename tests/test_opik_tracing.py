from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from core.opik_tracing import OpikConfigurationError, OpikTracer, opik_config_from_settings


@contextmanager
def _fake_trace(*_args, **_kwargs):
    yield object()


@contextmanager
def _fake_span(*_args, **_kwargs):
    yield object()


class OpikTracingTests(unittest.TestCase):
    def _settings(self) -> dict[str, str]:
        return {
            "dashboard_url": "http://localhost:5173",
            "workspace_name": "default",
            "project_id": "project-123",
            "project_name": "agent-workbench",
        }

    def test_easy__missing_config_fails_fast(self) -> None:
        with self.assertRaisesRegex(OpikConfigurationError, "opik.dashboard_url"):
            opik_config_from_settings({})

    def test_medium__api_url_uses_explicit_runtime_env(self) -> None:
        with patch.dict("os.environ", {"OPIK_URL_OVERRIDE": "http://opik.local/api"}, clear=False):
            cfg = opik_config_from_settings(self._settings())

        self.assertEqual(cfg.api_url, "http://opik.local/api")

    def test_medium__tracer_validates_opik_client(self) -> None:
        client = Mock()
        with (
            patch("core.opik_tracing.opik.configure") as configure,
            patch("core.opik_tracing.opik.Opik", return_value=client) as opik_client,
        ):
            tracer = OpikTracer(
                settings=self._settings(),
                change_id="WI-123",
                runner="copilot",
                model="gpt-5.5",
            )

        self.assertIs(tracer.client, client)
        configure.assert_called_once()
        opik_client.assert_called_once_with(project_name="agent-workbench", workspace="default")
        client.auth_check.assert_called_once()

    def test_medium__trace_and_span_emit_gui_events_and_update_context(self) -> None:
        client = Mock()
        events: list[tuple[str, dict]] = []

        def emit(event_type: str, **fields):
            events.append((event_type, fields))

        with (
            patch("core.opik_tracing.opik.configure"),
            patch("core.opik_tracing.opik.Opik", return_value=client),
            patch("core.opik_tracing.opik.start_as_current_trace", side_effect=_fake_trace) as start_trace,
            patch("core.opik_tracing.opik.start_as_current_span", side_effect=_fake_span) as start_span,
            patch("core.opik_tracing.opik_context.update_current_span") as update_span,
        ):
            tracer = OpikTracer(
                settings=self._settings(),
                change_id="WI-123",
                runner="copilot",
                model="gpt-5.5",
                emit_event=emit,
            )
            with tracer.trace("workflow:run", metadata={"stage": "workflow"}):
                with tracer.span("stage:intake", type="tool", metadata={"stage": "intake"}):
                    tracer.update_current_span(output={"ok": True})

        start_trace.assert_called_once()
        self.assertEqual(start_trace.call_args.kwargs["thread_id"], "WI-123")
        start_span.assert_called_once()
        update_span.assert_called_once()
        self.assertEqual([event_type for event_type, _ in events], ["opik.start", "opik.start", "opik.end", "opik.end"])
        self.assertEqual(events[0][1]["kind"], "trace")
        self.assertEqual(events[1][1]["kind"], "span")
        self.assertEqual(events[1][1]["depth"], 1)
        self.assertEqual(events[-1][1]["status"], "ok")

    def test_medium__span_error_event_preserves_exception(self) -> None:
        client = Mock()
        events: list[tuple[str, dict]] = []

        with (
            patch("core.opik_tracing.opik.configure"),
            patch("core.opik_tracing.opik.Opik", return_value=client),
            patch("core.opik_tracing.opik.start_as_current_span", side_effect=_fake_span),
        ):
            tracer = OpikTracer(
                settings=self._settings(),
                change_id="WI-123",
                runner="claude",
                model="claude-sonnet-4-6",
                emit_event=lambda event_type, **fields: events.append((event_type, fields)),
            )
            with self.assertRaisesRegex(ValueError, "boom"):
                with tracer.span("stage:qa", type="tool"):
                    raise ValueError("boom")

        self.assertEqual(events[-1][0], "opik.end")
        self.assertEqual(events[-1][1]["status"], "error")
        self.assertIn("ValueError: boom", events[-1][1]["error"])


if __name__ == "__main__":
    unittest.main()
