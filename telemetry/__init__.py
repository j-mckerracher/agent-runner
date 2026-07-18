"""Trace Contract v1: a versioned, locally persisted trace-event contract.

The canonical trace is a local JSONL file (see `event_sink.JsonlEventSink`).
External observability systems (Opik, etc.) are optional downstream sinks
and are never imported by this package. See `docs/tracing.md`.
"""
from __future__ import annotations

from telemetry.event_sink import EventSink, JsonlEventSink, SinkClosedError
from telemetry.events import (
    TRACE_SCHEMA_VERSION,
    EventStatus,
    EventType,
    MetadataSerializationError,
    TraceEvent,
    TraceValidationError,
    make_event,
    new_run_id,
    new_span_id,
)

__all__ = [
    "TRACE_SCHEMA_VERSION",
    "EventStatus",
    "EventType",
    "EventSink",
    "JsonlEventSink",
    "MetadataSerializationError",
    "SinkClosedError",
    "TraceEvent",
    "TraceValidationError",
    "make_event",
    "new_run_id",
    "new_span_id",
]
