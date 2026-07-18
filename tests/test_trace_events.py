"""Tests for telemetry/events.py — the versioned TraceEvent contract.

No real LLM calls, no network, no server, no Opik.
"""
from __future__ import annotations

import json

import pytest

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

FIXED_TS = "2026-07-17T10:00:00.000000Z"


def test_valid_event_creation() -> None:
    event = TraceEvent(event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id="run-1")
    assert event.event_type == EventType.RUN_STARTED
    assert event.event_schema_version == TRACE_SCHEMA_VERSION
    assert event.run_id == "run-1"


def test_missing_required_field_run_id_raises() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id="")
    assert any("run_id" in error for error in excinfo.value.errors)


def test_missing_required_fields_reports_all_offenders_in_one_pass() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(event_type="not.a.real.event", timestamp="not-a-timestamp", run_id="")
    fields_named = {error.split(":")[0] for error in excinfo.value.errors}
    assert fields_named == {"event_type", "timestamp", "run_id"}


def test_invalid_event_type_is_rejected() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(event_type="bogus.event", timestamp=FIXED_TS, run_id="run-1")
    assert any("event_type" in error for error in excinfo.value.errors)


def test_unsupported_schema_version_is_rejected_explicitly() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(
            event_type=EventType.RUN_STARTED,
            timestamp=FIXED_TS,
            run_id="run-1",
            event_schema_version="999",
        )
    assert any("event_schema_version" in error for error in excinfo.value.errors)


def test_optional_fields_omitted_are_absent_from_serialized_dict() -> None:
    event = TraceEvent(event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id="run-1")
    payload = event.to_dict()
    for optional_field in ("tokens_in", "tokens_out", "cost_usd", "duration_ms", "span_id"):
        assert optional_field not in payload


def test_optional_fields_populated_round_trip() -> None:
    event = TraceEvent(
        event_type=EventType.AGENT_INVOCATION_COMPLETED,
        timestamp=FIXED_TS,
        run_id="run-1",
        span_id="span-1",
        parent_span_id="span-0",
        agent="claude",
        runner="claude",
        model="claude-opus-4-8",
        status=EventStatus.OK,
        duration_ms=42.5,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
    )
    payload = event.to_dict()
    assert payload["span_id"] == "span-1"
    assert payload["parent_span_id"] == "span-0"
    assert payload["tokens_in"] == 100
    assert payload["tokens_out"] == 50
    assert payload["cost_usd"] == 0.01
    assert payload["duration_ms"] == 42.5
    assert payload["status"] == "ok"


def test_unknown_token_and_cost_metrics_remain_unknown_not_zero() -> None:
    event = TraceEvent(event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id="run-1")
    payload = event.to_dict()
    assert "tokens_in" not in payload
    assert "tokens_out" not in payload
    assert "cost_usd" not in payload
    assert "duration_ms" not in payload
    # And explicitly: an absent value is never rendered as a misleading 0.
    assert payload.get("tokens_in", "absent") != 0


def test_timestamp_must_be_timezone_aware_utc_iso8601() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(event_type=EventType.RUN_STARTED, timestamp="not-empty-but-invalid", run_id="run-1")
    assert any("timestamp" in error for error in excinfo.value.errors)


def test_naive_timestamp_without_offset_is_rejected() -> None:
    with pytest.raises(TraceValidationError) as excinfo:
        TraceEvent(event_type=EventType.RUN_STARTED, timestamp="2026-07-17T10:00:00", run_id="run-1")
    assert any("timestamp" in error for error in excinfo.value.errors)


def test_timestamp_with_z_suffix_and_with_explicit_offset_both_accepted() -> None:
    TraceEvent(event_type=EventType.RUN_STARTED, timestamp="2026-07-17T10:00:00Z", run_id="run-1")
    TraceEvent(event_type=EventType.RUN_STARTED, timestamp="2026-07-17T10:00:00+00:00", run_id="run-1")


def test_non_utc_offset_timestamp_is_rejected() -> None:
    with pytest.raises(TraceValidationError):
        TraceEvent(event_type=EventType.RUN_STARTED, timestamp="2026-07-17T10:00:00+05:00", run_id="run-1")


def test_json_serialization_is_deterministic() -> None:
    event = TraceEvent(
        event_type=EventType.RUN_STARTED,
        timestamp=FIXED_TS,
        run_id="run-1",
        metadata={"b": 2, "a": 1},
    )
    line_one = event.to_json()
    line_two = event.to_json()
    assert line_one == line_two
    parsed = json.loads(line_one)
    assert parsed["run_id"] == "run-1"


def test_to_dict_from_dict_round_trip() -> None:
    event = TraceEvent(
        event_type=EventType.STAGE_FAILED,
        timestamp=FIXED_TS,
        run_id="run-1",
        span_id="span-1",
        error_type="ValueError",
        error_message="boom",
    )
    payload = event.to_dict()
    rebuilt = TraceEvent.from_dict(payload)
    assert rebuilt.to_dict() == payload


def test_unknown_extra_top_level_keys_are_ignored_on_from_dict() -> None:
    payload = {
        "event_schema_version": TRACE_SCHEMA_VERSION,
        "event_type": "run.started",
        "timestamp": FIXED_TS,
        "run_id": "run-1",
        "some_future_field_v2": "ignored",
    }
    event = TraceEvent.from_dict(payload)
    assert not hasattr(event, "some_future_field_v2")
    assert event.run_id == "run-1"


def test_error_fields_round_trip() -> None:
    event = TraceEvent(
        event_type=EventType.TEST_FAILED,
        timestamp=FIXED_TS,
        run_id="run-1",
        error_type="AssertionError",
        error_message="expected 1 got 2",
    )
    payload = event.to_dict()
    assert payload["error_type"] == "AssertionError"
    assert payload["error_message"] == "expected 1 got 2"


def test_json_compatible_metadata_serializes() -> None:
    event = TraceEvent(
        event_type=EventType.ARTIFACT_CREATED,
        timestamp=FIXED_TS,
        run_id="run-1",
        metadata={"nested": {"list": [1, 2, "three"], "flag": True, "none": None}},
    )
    payload = event.to_dict()
    assert payload["metadata"]["nested"]["list"] == [1, 2, "three"]


def test_unserializable_metadata_raises_metadata_serialization_error() -> None:
    class NotJsonable:
        pass

    event = TraceEvent(
        event_type=EventType.ARTIFACT_CREATED,
        timestamp=FIXED_TS,
        run_id="run-1",
        metadata={"bad": NotJsonable()},
    )
    with pytest.raises(MetadataSerializationError) as excinfo:
        event.to_dict()
    message = str(excinfo.value)
    assert "run-1" in message
    assert "artifact.created" in message


def test_parent_and_child_span_relationship_is_representable() -> None:
    run_span = new_span_id()
    child_span = new_span_id()
    parent_event = TraceEvent(
        event_type=EventType.RUN_STARTED, timestamp=FIXED_TS, run_id="run-1", span_id=run_span
    )
    child_event = TraceEvent(
        event_type=EventType.STAGE_STARTED,
        timestamp=FIXED_TS,
        run_id="run-1",
        span_id=child_span,
        parent_span_id=run_span,
    )
    assert child_event.parent_span_id == parent_event.span_id
    assert run_span and child_span and run_span != child_span


def test_run_id_and_span_id_generators_are_nonempty_and_unique() -> None:
    run_a, run_b = new_run_id(), new_run_id()
    span_a, span_b = new_span_id(), new_span_id()
    assert run_a and run_b and run_a != run_b
    assert span_a and span_b and span_a != span_b


def test_make_event_is_the_single_generic_factory_for_all_event_types() -> None:
    for event_type in EventType:
        event = make_event(event_type, "run-1", timestamp=FIXED_TS)
        assert event.event_type == event_type


def test_make_event_defaults_timestamp_to_now_when_omitted() -> None:
    event = make_event(EventType.RUN_STARTED, "run-1")
    # "now" must still satisfy the tz-aware UTC validation performed in __post_init__.
    assert event.timestamp.endswith("Z")
