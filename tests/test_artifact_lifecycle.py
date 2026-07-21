"""Deterministic tests for `workflow.artifact_lifecycle` (Prompt 22).

Exercises the telemetry-emitting boundary with a local in-memory recording
sink — no real LLM, network, server, or Opik. Proves the per-reference event
mapping, payload rules (path vs URI, validation_status only on terminals),
missing/unexpected handling, None-sink no-op, and sink-failure precedence.
"""

from __future__ import annotations

from artifacts import (
    ArtifactRef,
    StoryArtifact,
    TaskPlanArtifact,
)
from telemetry import EventType
from workflow import RunContext, RunSpec
from workflow.artifact_lifecycle import emit_stage_artifact_lifecycle


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list = []
        self.closed = False

    def emit(self, event) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _RaisingSink(_RecordingSink):
    def emit(self, event) -> None:
        super().emit(event)
        raise RuntimeError("sink is unavailable")


def _types(sink: _RecordingSink) -> list[str]:
    return [e.event_type.value for e in sink.events]


def _story_output(producer: str = "intake", path: str | None = "/tmp/story.yaml") -> ArtifactRef:
    return ArtifactRef(
        artifact_type=StoryArtifact.ARTIFACT_TYPE,
        path=path,
        uri=None if path else "gs://bucket/story.yaml",
        producer_stage=producer,
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )


def _story_input() -> ArtifactRef:
    return ArtifactRef(
        artifact_type=StoryArtifact.ARTIFACT_TYPE,
        path="/tmp/story.yaml",
        producer_stage=StoryArtifact.PRODUCER_STAGE,
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )


# --------------------------------------------------------------------------- #
# Output ref lifecycle: created -> terminal                                   #
# --------------------------------------------------------------------------- #


def test_valid_output_emits_created_then_validated() -> None:
    sink = _RecordingSink()
    result = emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=sink, outputs=[_story_output()]
    )
    assert result.ok
    assert _types(sink) == ["artifact.created", "artifact.validated"]
    created, validated = sink.events
    assert created.metadata.get("validation_status") is None  # never on created
    assert created.artifact_path == "/tmp/story.yaml"
    assert validated.metadata["validation_status"] == "valid"


def test_invalid_output_emits_created_then_invalid() -> None:
    bad = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage="wrong-stage",
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    sink = _RecordingSink()
    result = emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=sink, outputs=[bad]
    )
    assert not result.ok
    assert _types(sink) == ["artifact.created", "artifact.invalid"]
    assert sink.events[1].metadata["validation_status"] == "invalid"
    assert "wrong_producer_stage" in sink.events[1].metadata["issue_codes"]


def test_uri_only_output_sets_no_artifact_path() -> None:
    sink = _RecordingSink()
    emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=sink, outputs=[_story_output(path=None)]
    )
    created = sink.events[0]
    assert created.artifact_path is None
    assert created.metadata["artifact_uri"] == "gs://bucket/story.yaml"


# --------------------------------------------------------------------------- #
# Input ref lifecycle: terminal only                                          #
# --------------------------------------------------------------------------- #


def test_valid_input_emits_only_validated() -> None:
    sink = _RecordingSink()
    # Supply a valid task_plan output too so nothing is missing.
    tp = ArtifactRef(
        artifact_type=TaskPlanArtifact.ARTIFACT_TYPE,
        path="/tmp/tp.yaml",
        producer_stage="task-generation",
        consumer_stages=tuple(TaskPlanArtifact.CONSUMER_STAGES),
        artifact_schema=TaskPlanArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=TaskPlanArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = emit_stage_artifact_lifecycle(
        run_id="run-1",
        stage="task-generation",
        sink=sink,
        inputs=[_story_input()],
        outputs=[tp],
    )
    assert result.ok
    # input: validated only (no created); output: created + validated.
    assert _types(sink) == ["artifact.validated", "artifact.created", "artifact.validated"]


# --------------------------------------------------------------------------- #
# Missing / unexpected                                                        #
# --------------------------------------------------------------------------- #


def test_missing_required_emits_artifact_missing_no_created() -> None:
    sink = _RecordingSink()
    # task-generation with nothing: story input missing + task_plan output missing.
    emit_stage_artifact_lifecycle(run_id="run-1", stage="task-generation", sink=sink)
    assert _types(sink) == ["artifact.missing", "artifact.missing"]
    for e in sink.events:
        assert e.artifact_path is None
        assert e.metadata["validation_status"] == "missing"
        assert "artifact_missing" in e.metadata["issue_codes"]


def test_unexpected_ref_emits_single_invalid() -> None:
    # Unexpected *input*: not produced by this stage, so no `created` — a
    # single terminal `artifact.invalid` only.
    sink = _RecordingSink()
    bad = ArtifactRef(artifact_type="qa_report", path="/tmp/x.yaml")
    # intake declares no inputs; also provide the required valid story output.
    emit_stage_artifact_lifecycle(
        run_id="run-1",
        stage="intake",
        sink=sink,
        inputs=[bad],
        outputs=[_story_output()],
    )
    assert _types(sink) == ["artifact.created", "artifact.validated", "artifact.invalid"]
    assert sink.events[-1].metadata["artifact_type"] == "qa_report"
    assert sink.events[-1].metadata["direction"] == "input"


def test_unexpected_output_emits_created_then_invalid() -> None:
    # Unexpected *output*: still produced by this stage, so it gets the same
    # created -> terminal shape as any other output, even though its type
    # matches no declared slot.
    sink = _RecordingSink()
    bad = ArtifactRef(artifact_type="qa_report", path="/tmp/x.yaml")
    emit_stage_artifact_lifecycle(
        run_id="run-1",
        stage="intake",
        sink=sink,
        outputs=[_story_output(), bad],
    )
    assert _types(sink) == [
        "artifact.created",
        "artifact.validated",
        "artifact.created",
        "artifact.invalid",
    ]
    unexpected_created, unexpected_invalid = sink.events[2], sink.events[3]
    assert unexpected_created.metadata["artifact_type"] == "qa_report"
    assert unexpected_created.metadata["direction"] == "output"
    assert unexpected_created.metadata.get("validation_status") is None  # never on created
    assert unexpected_created.artifact_path == "/tmp/x.yaml"
    assert unexpected_invalid.metadata["validation_status"] == "invalid"
    assert "unexpected_artifact_type" in unexpected_invalid.metadata["issue_codes"]
    # exactly one terminal event for the unexpected ref
    terminals = [e for e in sink.events[2:] if e.event_type.value in {"artifact.validated", "artifact.invalid"}]
    assert len(terminals) == 1


def test_exactly_one_terminal_per_supplied_ref() -> None:
    sink = _RecordingSink()
    emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=sink, outputs=[_story_output()]
    )
    terminals = [t for t in _types(sink) if t in {"artifact.validated", "artifact.invalid"}]
    assert len(terminals) == 1


# --------------------------------------------------------------------------- #
# Sink ownership / precedence                                                 #
# --------------------------------------------------------------------------- #


def test_none_sink_runs_validation_without_emitting() -> None:
    result = emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=None, outputs=[_story_output()]
    )
    assert result.ok
    assert result.stage_name == "intake"


def test_sink_failure_does_not_change_result() -> None:
    good = _RecordingSink()
    bad = _RaisingSink()
    ref = _story_output()
    good_result = emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=good, outputs=[ref]
    )
    bad_result = emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=bad, outputs=[ref]
    )
    assert bad_result.to_dict() == good_result.to_dict()
    # Raising sink never closed by the boundary.
    assert bad.closed is False


def test_boundary_never_closes_caller_sink() -> None:
    sink = _RecordingSink()
    emit_stage_artifact_lifecycle(
        run_id="run-1", stage="intake", sink=sink, outputs=[_story_output()]
    )
    assert sink.closed is False


def test_events_carry_run_id_and_stage() -> None:
    sink = _RecordingSink()
    emit_stage_artifact_lifecycle(
        run_id="run-42", stage="intake", sink=sink, outputs=[_story_output()]
    )
    for e in sink.events:
        assert e.run_id == "run-42"
        assert e.stage == "intake"


def test_fixture_integration_with_run_context() -> None:
    """Fixture-only proof that the boundary composes with the real `RunContext`
    shell contract — canonical stage name, `RunContext.for_spec`-issued run_id,
    a fake sink. No production orchestration wired; RunContext is not mutated
    beyond its own public `current_stage` field."""
    spec = RunSpec()
    context = RunContext.for_spec(spec, run_id="run-int-1")
    context.current_stage = "intake"

    sink = _RecordingSink()
    result = emit_stage_artifact_lifecycle(
        run_id=context.run_id,
        stage=context.current_stage,
        sink=sink,
        outputs=[_story_output()],
    )

    assert result.ok
    assert result.stage_name == "intake"
    assert _types(sink) == ["artifact.created", "artifact.validated"]
    for event in sink.events:
        assert event.run_id == context.run_id == "run-int-1"
        assert event.stage == context.current_stage == "intake"


def test_declaration_argument_overrides_stage_name() -> None:
    from workflow import get_stage_declaration

    decl = get_stage_declaration("intake")
    sink = _RecordingSink()
    result = emit_stage_artifact_lifecycle(
        run_id="run-1", sink=sink, declaration=decl, outputs=[_story_output()]
    )
    assert result.stage_name == "intake"
    assert EventType.ARTIFACT_CREATED.value in _types(sink)
