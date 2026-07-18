"""Thin integration proof for Trace Contract v1.

`run_demo_trace` is a deterministic, fixture-driven fake execution path — it
performs no real workflow orchestration, no LLM calls, and touches no
server or Opik code. Its only job is to prove that a `JsonlEventSink` fed
by `telemetry.events` can produce a canonical local trace containing a
run-start event, at least one nested lifecycle event (a stage and a test),
and a terminal run event, with parent/child span relationships represented.

This is intentionally not wired into `eval/runner.py` or `run.py`. See
`docs/tracing.md` for which production lifecycle points remain
uninstrumented after this prompt.
"""
from __future__ import annotations

from pathlib import Path

from telemetry.event_sink import JsonlEventSink
from telemetry.events import EventStatus, EventType, make_event, new_run_id, new_span_id


def run_demo_trace(
    path: str | Path,
    *,
    run_id: str | None = None,
    run_span_id: str | None = None,
    stage_span_id: str | None = None,
    timestamps: list[str] | None = None,
) -> Path:
    """Emit a deterministic run -> stage -> test -> run trace to `path`.

    `run_id`/`run_span_id`/`stage_span_id` and `timestamps` (a list of 6
    ISO-8601 strings, one per emitted event, in order) can be injected so
    tests can assert on exact content; when omitted, fresh IDs and "now"
    timestamps are used.

    Returns the resolved `Path` the trace was written to.
    """
    run_id = run_id or new_run_id()
    run_span_id = run_span_id or new_span_id()
    stage_span_id = stage_span_id or new_span_id()
    ts = timestamps or [None] * 6  # type: ignore[list-item]
    if len(ts) != 6:
        raise ValueError("timestamps must contain exactly 6 entries when provided")

    sink_path = Path(path)
    with JsonlEventSink(sink_path) as sink:
        sink.emit(
            make_event(
                EventType.RUN_STARTED,
                run_id,
                timestamp=ts[0],
                span_id=run_span_id,
                status=EventStatus.OK,
                metadata={"source": "telemetry.demo.run_demo_trace"},
            )
        )
        sink.emit(
            make_event(
                EventType.STAGE_STARTED,
                run_id,
                timestamp=ts[1],
                span_id=stage_span_id,
                parent_span_id=run_span_id,
                stage="demo-stage",
                status=EventStatus.OK,
            )
        )
        sink.emit(
            make_event(
                EventType.TEST_STARTED,
                run_id,
                timestamp=ts[2],
                parent_span_id=stage_span_id,
                stage="demo-stage",
                status=EventStatus.OK,
            )
        )
        sink.emit(
            make_event(
                EventType.TEST_COMPLETED,
                run_id,
                timestamp=ts[3],
                parent_span_id=stage_span_id,
                stage="demo-stage",
                status=EventStatus.OK,
                duration_ms=1.5,
            )
        )
        sink.emit(
            make_event(
                EventType.STAGE_COMPLETED,
                run_id,
                timestamp=ts[4],
                span_id=stage_span_id,
                parent_span_id=run_span_id,
                stage="demo-stage",
                status=EventStatus.OK,
            )
        )
        sink.emit(
            make_event(
                EventType.RUN_COMPLETED,
                run_id,
                timestamp=ts[5],
                span_id=run_span_id,
                status=EventStatus.OK,
            )
        )
        sink.flush()

    return sink_path
