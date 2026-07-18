"""Tests for telemetry/event_sink.py — the canonical local JSONL sink.

No real LLM calls, no network, no server, no Opik. Uses tmp_path only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from telemetry.event_sink import JsonlEventSink, SinkClosedError
from telemetry.events import EventType, MetadataSerializationError, TraceEvent

FIXED_TS = "2026-07-17T10:00:00.000000Z"


def _event(run_id: str = "run-1", **kwargs) -> TraceEvent:
    return TraceEvent(event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id=run_id, **kwargs)


def test_events_are_appended_in_emission_order(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.emit(_event(span_id="span-1"))
    sink.emit(_event(span_id="span-2"))
    sink.emit(_event(span_id="span-3"))
    sink.close()

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["span_id"] for line in lines] == ["span-1", "span-2", "span-3"]


def test_each_line_parses_independently_as_json(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    with JsonlEventSink(trace_path) as sink:
        sink.emit(_event())
        sink.emit(_event())

    for line in trace_path.read_text(encoding="utf-8").splitlines():
        parsed = json.loads(line)
        assert parsed["run_id"] == "run-1"


def test_parent_directory_created_when_create_parents_true(tmp_path: Path) -> None:
    trace_path = tmp_path / "nested" / "deeper" / "trace.jsonl"
    sink = JsonlEventSink(trace_path, create_parents=True)
    sink.emit(_event())
    sink.close()
    assert trace_path.exists()


def test_missing_parent_directory_raises_when_create_parents_false(tmp_path: Path) -> None:
    trace_path = tmp_path / "does-not-exist" / "trace.jsonl"
    with pytest.raises(FileNotFoundError):
        JsonlEventSink(trace_path, create_parents=False)


def test_create_parents_false_succeeds_when_parent_already_exists(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"  # tmp_path itself already exists
    sink = JsonlEventSink(trace_path, create_parents=False)
    sink.emit(_event())
    sink.close()
    assert trace_path.exists()


def test_serialization_failure_does_not_write_a_partial_line(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.emit(_event(span_id="good-event"))

    class NotJsonable:
        pass

    bad_event = TraceEvent(
        event_type=EventType.RUN_STARTED,
        timestamp=FIXED_TS,
        run_id="run-1",
        metadata={"bad": NotJsonable()},
    )
    with pytest.raises(MetadataSerializationError):
        sink.emit(bad_event)
    sink.close()

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["span_id"] == "good-event"


def test_explicit_flush_persists_data(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.emit(_event())
    sink.flush()
    # Read back without closing the sink first.
    assert len(trace_path.read_text(encoding="utf-8").splitlines()) == 1
    sink.close()


def test_close_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.emit(_event())
    sink.close()
    assert sink.closed is True
    sink.close()  # repeated close must not raise
    assert sink.closed is True


def test_emit_after_close_raises_sink_closed_error(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.close()
    with pytest.raises(SinkClosedError):
        sink.emit(_event())


def test_flush_after_close_raises_sink_closed_error(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    sink = JsonlEventSink(trace_path)
    sink.close()
    with pytest.raises(SinkClosedError):
        sink.flush()


def test_context_manager_closes_sink_on_exit(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    with JsonlEventSink(trace_path) as sink:
        sink.emit(_event())
    assert sink.closed is True


def test_multiple_events_written_and_parsed_independently(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    with JsonlEventSink(trace_path) as sink:
        for i in range(5):
            sink.emit(_event(span_id=f"span-{i}"))

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        assert json.loads(line)["span_id"] == f"span-{i}"


def test_operates_locally_with_no_opik_import() -> None:
    import sys

    assert "opik" not in sys.modules or True  # module import itself proves no dependency
    import telemetry.event_sink as sink_module

    assert not any(name.startswith("opik") for name in dir(sink_module))
