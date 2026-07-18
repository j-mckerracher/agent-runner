# Trace Contract v1

This document describes the versioned, locally persisted trace-event
contract implemented in `telemetry/`. It is additive: nothing in
`eval/runner.py`, `run.py`, `server/`, or the Opik integration changes as
part of this contract, and no existing command or workflow behavior is
affected.

## Why local JSONL is the source of truth

The canonical trace is a locally persisted JSONL file, one `TraceEvent`
per line, written by `telemetry.event_sink.JsonlEventSink`. External
observability systems — Opik, HALO/OTLP exporters, dashboards — are
optional *downstream* consumers of that file, never the source of truth.
This means:

- Core trace functionality (creating events, writing/reading the trace)
  works with no server running, no cloud services, and no optional vendor
  SDK installed.
- `telemetry/events.py` and `telemetry/event_sink.py` import nothing from
  `server`, `core.opik*`, or the `opik` package. Removing Opik entirely
  from the environment does not break tracing.
- Existing vendor-coupled mechanisms (`server/events.py`'s JSONL event
  log + SSE bus, `core/opik_tracing.py`, `core/ui_trace_bridge.py`) are
  unaffected and are not required to adopt this contract.

## Schema version

`telemetry.events.TRACE_SCHEMA_VERSION = "1"`. Every `TraceEvent` carries
`event_schema_version` (defaults to the current version). Values outside
`telemetry.events.SUPPORTED_SCHEMA_VERSIONS` are rejected explicitly at
construction time (`TraceValidationError`) rather than silently parsed as
if they matched the current contract.

Future schema evolution: bump `TRACE_SCHEMA_VERSION`, add the new value to
`SUPPORTED_SCHEMA_VERSIONS` if the module can still validate/read it, and
document the delta here. `TraceEvent.from_dict` ignores unknown top-level
keys on read (forward-compatibility) — put anything non-standard in
`metadata` instead of inventing new top-level fields.

## Required fields

Every `TraceEvent` requires:

- `event_schema_version` — schema version string.
- `event_type` — one of the supported families (see below).
- `timestamp` — timezone-aware UTC ISO-8601 string (e.g.
  `2026-07-17T10:00:00.000000Z` or `...+00:00`). A naive timestamp or a
  non-UTC offset is rejected, not just "any nonempty string".
- `run_id` — nonempty string identifying the run this event belongs to.

Missing or invalid required fields raise `telemetry.events.TraceValidationError`,
which carries every offending field (not just the first) in `.errors`.

## Optional fields

All optional fields default to `None` and are **omitted entirely** from
the serialized JSON when unset — `to_dict()` never renders an unknown
value as `0`, `""`, or `false`. This applies especially to `tokens_in`,
`tokens_out`, `cost_usd`, and `duration_ms`: "unknown" and "measured as
zero" are always distinguishable by checking whether the key is present.

Supported optional fields: `span_id`, `parent_span_id`, `stage`, `agent`,
`runner`, `model`, `status`, `duration_ms`, `artifact_path`,
`prompt_sha256`, `response_sha256`, `tokens_in`, `tokens_out`, `cost_usd`,
`error_type`, `error_message`, `metadata` (a JSON-compatible dict, empty
by default).

## Supported event families

Centralized in `telemetry.events.EventType`:

```
run.started                     stage.started                    agent.invocation.started
run.completed                    stage.completed                   agent.invocation.completed
run.failed                       stage.failed                      agent.invocation.failed

artifact.created                 test.started
artifact.validated                test.completed
artifact.invalid                  test.failed
```

None of these are required to be wired into production code by this
prompt — the contract just makes them representable and testable.

## Trace and span identifiers

`telemetry.events.new_run_id()` / `new_span_id()` return nonempty,
unique, uuid4-derived strings (`run-<hex>` / `span-<hex>`). Parent/child
relationships are represented by setting a child event's `parent_span_id`
to the parent's `span_id` — see `telemetry/demo.py` for a worked example
(a run span with a nested stage span, and further-nested test events).
This is intentionally not a full distributed-tracing implementation.

## How to emit an event

```python
from telemetry.event_sink import JsonlEventSink
from telemetry.events import EventType, EventStatus, make_event, new_run_id, new_span_id

run_id = new_run_id()
run_span = new_span_id()

with JsonlEventSink("path/to/trace.jsonl") as sink:
    sink.emit(make_event(EventType.RUN_STARTED, run_id, span_id=run_span, status=EventStatus.OK))
    # ... do work ...
    sink.emit(make_event(EventType.RUN_COMPLETED, run_id, span_id=run_span, status=EventStatus.OK))
```

`make_event` is the single generic factory used for all 15 event
families — pass the desired `EventType` and only the fields that apply.
`timestamp` defaults to "now" (UTC); pass an explicit value for
deterministic tests.

## How to inspect and parse the JSONL file

Each line is an independently parseable JSON object:

```python
import json

with open("path/to/trace.jsonl", encoding="utf-8") as f:
    events = [json.loads(line) for line in f]
```

## Flush and close behavior

- `JsonlEventSink.flush()` calls the underlying file handle's `flush()` —
  deterministic, no buffering surprises.
- `JsonlEventSink.close()` flushes and closes the handle, and is
  idempotent: calling it more than once is a no-op.
- `emit()`/`flush()` after `close()` raise `telemetry.event_sink.SinkClosedError`.
- `JsonlEventSink` is a context manager (`with JsonlEventSink(path) as sink:`)
  and calls `close()` on exit.
- Parent directories: `create_parents=True` (default) creates them;
  `create_parents=False` requires the parent directory to already exist
  and raises `FileNotFoundError` otherwise — an intentional choice, not an
  implicit mkdir.

## How unknown metrics are represented

Absent, never zero. If `tokens_in` was never measured, the serialized
event simply has no `tokens_in` key. Consumers must treat a missing key
as "unknown", not as `0`.

## Serialization failure behavior

`TraceEvent.to_dict()` (and therefore `to_json()`) validates `metadata`
recursively against JSON-compatible types before returning. Unsupported
values raise `telemetry.events.MetadataSerializationError`, naming the
offending event (`run_id`, `event_type`, `span_id`) and the path inside
`metadata` — values are never silently stringified as a fallback.

`JsonlEventSink.emit()` serializes the complete line (`event.to_json()`)
*before* writing anything to the file. If serialization raises, nothing
is written — a failed event can never leave a partial JSON line in the
trace, and any events already written earlier are untouched.

## What production lifecycle points are — and are not — instrumented

This prompt does not instrument any real lifecycle point in `run.py` or
`eval/runner.py`. The only integration proof is
`telemetry.demo.run_demo_trace`, a deterministic fixture flow that emits
a `run.started` → nested `stage.started`/`test.started`/`test.completed`/
`stage.completed` → `run.completed` sequence to prove the contract, sink,
and span nesting work end-to-end without a server, LLM calls, or Opik.
Wiring the contract into real workflow orchestration is explicitly out of
scope here and left for a future prompt.

## Backward-compatibility expectations

- Adding new optional fields or new `EventType` members in a future
  minor revision should not require bumping `TRACE_SCHEMA_VERSION` as
  long as older readers can ignore fields they don't understand
  (`from_dict` already does this for unknown top-level keys).
- Changing the *meaning* of an existing field, or removing a required
  field, requires a new `TRACE_SCHEMA_VERSION` and an entry in
  `SUPPORTED_SCHEMA_VERSIONS` for however long both versions must be
  readable.
- Existing event/telemetry mechanisms (`server/events.py`,
  `core/opik_tracing.py`, `core/ui_trace_bridge.py`) are unaffected and
  are not required to migrate.
