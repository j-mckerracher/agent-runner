"""Thin integration proof for Trace Contract v1.

Verifies that a deterministic fixture flow (`telemetry.demo.run_demo_trace`)
produces a canonical local JSONL trace with a run-start event, at least one
nested lifecycle event, and a terminal run event — with no server, no LLM
calls, and no Opik involved.

Also verifies (in a clean subprocess, not just via `sys.modules` in the
current pytest process) that the trace-contract modules import cleanly with
optional observability dependencies unavailable and do not themselves pull
in `opik` or `server`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from telemetry.demo import run_demo_trace
from telemetry.events import EventType

FIXED_TIMESTAMPS = [
    "2026-07-17T10:00:00.000000Z",
    "2026-07-17T10:00:00.100000Z",
    "2026-07-17T10:00:00.200000Z",
    "2026-07-17T10:00:00.300000Z",
    "2026-07-17T10:00:00.400000Z",
    "2026-07-17T10:00:00.500000Z",
]


def test_demo_trace_contains_run_nested_and_terminal_events(tmp_path: Path) -> None:
    trace_path = run_demo_trace(
        tmp_path / "demo.jsonl",
        run_id="run-fixed",
        run_span_id="span-run",
        stage_span_id="span-stage",
        timestamps=FIXED_TIMESTAMPS,
    )

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6

    events = [json.loads(line) for line in lines]
    event_types = [event["event_type"] for event in events]
    assert event_types == [
        EventType.RUN_STARTED.value,
        EventType.STAGE_STARTED.value,
        EventType.TEST_STARTED.value,
        EventType.TEST_COMPLETED.value,
        EventType.STAGE_COMPLETED.value,
        EventType.RUN_COMPLETED.value,
    ]

    # Required fields present on every line, independently.
    for event in events:
        assert event["event_schema_version"]
        assert event["event_type"]
        assert event["timestamp"]
        assert event["run_id"] == "run-fixed"

    # Deterministic: identical inputs produce byte-identical output.
    trace_path_2 = run_demo_trace(
        tmp_path / "demo2.jsonl",
        run_id="run-fixed",
        run_span_id="span-run",
        stage_span_id="span-stage",
        timestamps=FIXED_TIMESTAMPS,
    )
    assert trace_path.read_text(encoding="utf-8") == trace_path_2.read_text(encoding="utf-8")

    # Parent/child span relationships are represented.
    run_started, stage_started = events[0], events[1]
    assert run_started["span_id"] == "span-run"
    assert stage_started["parent_span_id"] == run_started["span_id"]
    test_started = events[2]
    assert test_started["parent_span_id"] == "span-stage"

    # Unknown metrics stay absent, never a misleading zero.
    for event in events:
        assert "tokens_in" not in event
        assert "tokens_out" not in event
        assert "cost_usd" not in event


def test_demo_trace_requires_no_external_observability_service(tmp_path: Path) -> None:
    # No server started, no Opik client configured, no network access used —
    # the only side effect is a local file write.
    import sys as _sys

    before = set(_sys.modules)
    run_demo_trace(tmp_path / "demo.jsonl")
    after = set(_sys.modules)
    newly_imported = after - before
    assert not any(name == "opik" or name.startswith("opik.") for name in newly_imported)
    assert not any(name == "server" or name.startswith("server.") for name in newly_imported)


def test_optional_dependency_import_isolation_in_clean_subprocess() -> None:
    """Import the trace-contract modules in a fresh interpreter and assert
    they succeed and never import `opik` or `server`, independent of
    whatever is already loaded in the current pytest process."""
    script = (
        "import sys; "
        "import telemetry, telemetry.events, telemetry.event_sink, telemetry.demo; "
        "assert 'opik' not in sys.modules, 'telemetry package must not import opik'; "
        "assert 'server' not in sys.modules, 'telemetry package must not import server'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == "OK"
