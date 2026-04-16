"""Tests for shared models, events, and schemas."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner_shared.events import (
    EVENT_PREFIX,
    EVENT_VERSION,
    emit_event_line,
    parse_event_line,
)
from agent_runner_shared.models import AgentRef, BaselineBand, RunLineage, WorkflowRef


def test_event_round_trip() -> None:
    line = emit_event_line("run.start", run_id="r1", stage="intake", foo="bar")
    assert line.startswith(EVENT_PREFIX)
    payload = parse_event_line(line)
    assert payload is not None
    assert payload["kind"] == "run.start"
    assert payload["event_version"] == EVENT_VERSION
    assert payload["data"]["foo"] == "bar"


def test_parse_event_unknown_version_raises() -> None:
    bogus = f"{EVENT_PREFIX} " + json.dumps({"event_version": "99", "kind": "x"})
    with pytest.raises(ValueError):
        parse_event_line(bogus)


def test_parse_non_event_returns_none() -> None:
    assert parse_event_line("just some log line") is None


def test_agent_ref_parse_ok() -> None:
    ref = AgentRef.parse("intake@v1")
    assert ref.name == "intake" and ref.version == "v1"
    assert str(ref) == "intake@v1"


def test_agent_ref_parse_invalid() -> None:
    with pytest.raises(ValueError):
        AgentRef.parse("intake-v1")


def test_baseline_band_roundtrip() -> None:
    band = BaselineBand(
        task_id="t1",
        task_version=1,
        low=0.4,
        high=0.7,
        mean=0.55,
        sample_size=5,
        judge_model="gpt-5.4-high",
    )
    data = band.model_dump()
    assert data["low"] == 0.4


def test_lineage_minimum_fields() -> None:
    lin = RunLineage(
        run_id="run-1",
        runner_version="0.1.0",
        workflow=WorkflowRef(id="standard", version=1),
        agent_versions=[AgentRef(name="intake", version="v1")],
        task_id="t1",
        task_version=1,
        substrate_commit="abc123",
        substrate_ref="baseline-2026-04-16",
        models={"worker": "claude-sonnet-4.6"},
        judge_model="gpt-5.4-high",
        cassette_mode="live",
    )
    assert lin.k == 1


def test_task_schema_is_valid_json_schema() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "agent_runner_shared"
        / "schemas"
        / "task.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(schema)


def test_workflow_schema_is_valid_json_schema() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "agent_runner_shared"
        / "schemas"
        / "workflow.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(schema)
