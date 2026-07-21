"""Deterministic tests for `workflow.stage_artifacts` (Prompt 22).

Pure declaration/registry/validation — no sink, no telemetry, no LLM, no
network. Covers the canonical registry/matrix, registry-build guards,
cardinality shapes, and the direction-specific per-reference validation
semantics (missing vs invalid vs unexpected, absent-metadata-as-warning).
"""

from __future__ import annotations

import pytest

from artifacts import (
    ArtifactRef,
    ArtifactValidationStatus,
    AssignmentArtifact,
    ImplementationReportArtifact,
    QAReportArtifact,
    StoryArtifact,
    TaskPlanArtifact,
    UowSpecArtifact,
    ValidationSeverity,
)
from core.workflow_constants import WORKFLOW_STAGES
from workflow.stage_artifacts import (
    ISSUE_CARDINALITY_TOO_MANY,
    ISSUE_INCOMPATIBLE_CONSUMER,
    ISSUE_MISSING_REQUIRED,
    ISSUE_SCHEMA_MISMATCH,
    ISSUE_SCHEMA_VERSION_MISMATCH,
    ISSUE_UNEXPECTED_TYPE,
    ISSUE_WRONG_PRODUCER,
    STAGE_ARTIFACT_REGISTRY,
    WARNING_UNKNOWN_CONSUMER,
    WARNING_UNKNOWN_PRODUCER,
    WARNING_UNKNOWN_SCHEMA,
    ArtifactDirection,
    Cardinality,
    StageArtifactDeclaration,
    StageArtifactRegistryError,
    StageArtifactSpec,
    UnknownStageError,
    get_stage_declaration,
    validate_stage_artifacts,
)

# --------------------------------------------------------------------------- #
# Ref builders                                                                #
# --------------------------------------------------------------------------- #

_ARTIFACTS = {
    "story": StoryArtifact,
    "task_plan": TaskPlanArtifact,
    "assignment": AssignmentArtifact,
    "uow_spec": UowSpecArtifact,
    "impl_report": ImplementationReportArtifact,
    "qa_report": QAReportArtifact,
}


def _valid_input_ref(artifact_type: str, consuming_stage: str) -> ArtifactRef:
    cls = _ARTIFACTS[artifact_type]
    return ArtifactRef(
        artifact_type=cls.ARTIFACT_TYPE,
        path=f"/tmp/{artifact_type}.yaml",
        producer_stage=cls.PRODUCER_STAGE,
        consumer_stages=tuple(cls.CONSUMER_STAGES),
        artifact_schema=cls.ARTIFACT_SCHEMA,
        artifact_schema_version=cls.ARTIFACT_SCHEMA_VERSION,
    )


def _valid_output_ref(artifact_type: str, producing_stage: str) -> ArtifactRef:
    cls = _ARTIFACTS[artifact_type]
    return ArtifactRef(
        artifact_type=cls.ARTIFACT_TYPE,
        path=f"/tmp/{artifact_type}.yaml",
        producer_stage=producing_stage,
        consumer_stages=tuple(cls.CONSUMER_STAGES),
        artifact_schema=cls.ARTIFACT_SCHEMA,
        artifact_schema_version=cls.ARTIFACT_SCHEMA_VERSION,
    )


# --------------------------------------------------------------------------- #
# Registry / matrix                                                           #
# --------------------------------------------------------------------------- #


def test_every_canonical_stage_registered_exactly_once() -> None:
    assert tuple(STAGE_ARTIFACT_REGISTRY) == tuple(WORKFLOW_STAGES)
    assert len(STAGE_ARTIFACT_REGISTRY) == len(set(WORKFLOW_STAGES))


def test_matrix_matches_expected_consume_produce_shape() -> None:
    expected = {
        "materialize": ([], []),
        "intake": ([], [("story", "exactly_one")]),
        "task-generation": ([("story", "exactly_one")], [("task_plan", "exactly_one")]),
        "task-assignment": (
            [("story", "exactly_one"), ("task_plan", "exactly_one")],
            [("assignment", "exactly_one"), ("uow_spec", "one_or_more")],
        ),
        "execution": (
            [("assignment", "exactly_one"), ("uow_spec", "one_or_more")],
            [("impl_report", "one_or_more")],
        ),
        "qa": (
            [
                ("story", "exactly_one"),
                ("task_plan", "exactly_one"),
                ("assignment", "exactly_one"),
                ("impl_report", "one_or_more"),
            ],
            [("qa_report", "exactly_one")],
        ),
        "pr-review": (
            [
                ("story", "exactly_one"),
                ("task_plan", "exactly_one"),
                ("assignment", "exactly_one"),
                ("impl_report", "one_or_more"),
                ("qa_report", "exactly_one"),
            ],
            [],
        ),
    }
    for stage, (want_in, want_out) in expected.items():
        decl = get_stage_declaration(stage)
        got_in = [(s.artifact_type, s.cardinality.label) for s in decl.inputs]
        got_out = [(s.artifact_type, s.cardinality.label) for s in decl.outputs]
        assert got_in == want_in, stage
        assert got_out == want_out, stage


def test_declaration_to_dict_is_deterministic() -> None:
    decl = get_stage_declaration("qa")
    assert decl.to_dict() == decl.to_dict()
    payload = decl.to_dict()
    assert payload["stage"] == "qa"
    assert [i["artifact_type"] for i in payload["inputs"]] == [
        "story",
        "task_plan",
        "assignment",
        "impl_report",
    ]


def test_empty_declarations_have_no_specs() -> None:
    assert get_stage_declaration("materialize").inputs == ()
    assert get_stage_declaration("materialize").outputs == ()
    assert get_stage_declaration("pr-review").outputs == ()


def test_unknown_stage_raises_structured_error() -> None:
    with pytest.raises(UnknownStageError) as exc:
        get_stage_declaration("nope")
    assert exc.value.stage_name == "nope"
    assert "known stages" in str(exc.value)


def test_producer_consumer_parity_holds_for_registry() -> None:
    for stage, decl in STAGE_ARTIFACT_REGISTRY.items():
        for spec in decl.outputs:
            assert spec.producer_stage == stage
        for spec in decl.inputs:
            assert stage in spec.consumer_stages


# --------------------------------------------------------------------------- #
# Registry-build guards                                                       #
# --------------------------------------------------------------------------- #


def test_duplicate_spec_in_direction_rejected() -> None:
    from workflow.stage_artifacts import _build_declaration  # type: ignore

    with pytest.raises(StageArtifactRegistryError):
        _build_declaration(
            "task-generation",
            [
                (StoryArtifact, ArtifactDirection.INPUT, Cardinality.EXACTLY_ONE),
                (StoryArtifact, ArtifactDirection.INPUT, Cardinality.EXACTLY_ONE),
            ],
            [],
        )


def test_contradictory_producer_rejected() -> None:
    from workflow.stage_artifacts import _build_declaration  # type: ignore

    # intake produces story, but claim task-generation produces it.
    with pytest.raises(StageArtifactRegistryError):
        _build_declaration(
            "task-generation",
            [],
            [(StoryArtifact, ArtifactDirection.OUTPUT, Cardinality.EXACTLY_ONE)],
        )


def test_contradictory_consumer_rejected() -> None:
    from workflow.stage_artifacts import _build_declaration  # type: ignore

    # qa_report is consumed only by pr-review; claim intake consumes it.
    with pytest.raises(StageArtifactRegistryError):
        _build_declaration(
            "intake",
            [(QAReportArtifact, ArtifactDirection.INPUT, Cardinality.EXACTLY_ONE)],
            [],
        )


def test_invalid_cardinality_rejected() -> None:
    with pytest.raises(StageArtifactRegistryError):
        Cardinality(-1, 1)
    with pytest.raises(StageArtifactRegistryError):
        Cardinality(2, 1)


def test_cardinality_labels_and_allows() -> None:
    assert Cardinality.ZERO.label == "zero"
    assert Cardinality.EXACTLY_ONE.label == "exactly_one"
    assert Cardinality.OPTIONAL_ONE.label == "optional_one"
    assert Cardinality.ONE_OR_MORE.label == "one_or_more"
    assert Cardinality.EXACTLY_ONE.allows(1)
    assert not Cardinality.EXACTLY_ONE.allows(2)
    assert not Cardinality.EXACTLY_ONE.allows(0)
    assert Cardinality.ONE_OR_MORE.allows(5)
    assert Cardinality.OPTIONAL_ONE.allows(0)


# --------------------------------------------------------------------------- #
# Validation — happy paths                                                    #
# --------------------------------------------------------------------------- #


def test_valid_inputs_and_outputs_for_every_stage() -> None:
    for stage, decl in STAGE_ARTIFACT_REGISTRY.items():
        inputs = [_valid_input_ref(s.artifact_type, stage) for s in decl.inputs]
        outputs = [_valid_output_ref(s.artifact_type, stage) for s in decl.outputs]
        result = validate_stage_artifacts(stage, inputs=inputs, outputs=outputs)
        assert result.ok, (stage, [i.to_dict() for i in result.errors])
        assert result.unexpected == ()


def test_optional_absent_slot_produces_no_issue() -> None:
    decl = StageArtifactDeclaration(
        stage_name="synthetic",
        inputs=(
            StageArtifactSpec.from_artifact(
                StoryArtifact, ArtifactDirection.INPUT, Cardinality.OPTIONAL_ONE
            ),
        ),
    )
    result = validate_stage_artifacts(decl)
    assert result.ok
    slot = result.slots[0]
    assert slot.status is ArtifactValidationStatus.VALID
    assert slot.issues == ()


# --------------------------------------------------------------------------- #
# Validation — failure paths                                                  #
# --------------------------------------------------------------------------- #


def test_missing_required_input_and_output() -> None:
    # task-generation: required story input, required task_plan output.
    result = validate_stage_artifacts("task-generation")
    assert not result.ok
    missing = {(s.direction.value, s.spec.artifact_type): s.status for s in result.slots}
    assert missing[("input", "story")] is ArtifactValidationStatus.MISSING
    assert missing[("output", "task_plan")] is ArtifactValidationStatus.MISSING
    assert all(i.code == ISSUE_MISSING_REQUIRED for i in result.errors)


def test_unexpected_type_is_invalid_not_missing() -> None:
    bad = ArtifactRef(artifact_type="qa_report", path="/tmp/x")
    result = validate_stage_artifacts("intake", inputs=[bad])
    assert not result.ok
    assert len(result.unexpected) == 1
    u = result.unexpected[0]
    assert u.status is ArtifactValidationStatus.INVALID
    assert u.direction is ArtifactDirection.INPUT
    assert u.issues[0].code == ISSUE_UNEXPECTED_TYPE


def test_wrong_schema_and_version_are_errors() -> None:
    ref = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage=StoryArtifact.PRODUCER_STAGE,
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema="wrong.schema",
        artifact_schema_version="999",
    )
    result = validate_stage_artifacts("task-generation", inputs=[ref])
    codes = {i.code for i in result.errors}
    assert ISSUE_SCHEMA_MISMATCH in codes
    assert ISSUE_SCHEMA_VERSION_MISMATCH in codes


def test_input_wrong_producer_is_error() -> None:
    ref = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage="not-intake",
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = validate_stage_artifacts("task-generation", inputs=[ref])
    assert ISSUE_WRONG_PRODUCER in {i.code for i in result.errors}


def test_input_incompatible_consumer_is_error() -> None:
    ref = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage=StoryArtifact.PRODUCER_STAGE,
        consumer_stages=("materialize",),  # task-generation not listed
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = validate_stage_artifacts("task-generation", inputs=[ref])
    assert ISSUE_INCOMPATIBLE_CONSUMER in {i.code for i in result.errors}


def test_output_producer_must_be_current_stage() -> None:
    # An output whose producer_stage is not the producing stage is wrong.
    ref = _valid_output_ref("story", "intake")
    ref = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage="qa",  # not the producing stage
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = validate_stage_artifacts("intake", outputs=[ref])
    assert ISSUE_WRONG_PRODUCER in {i.code for i in result.errors}


def test_output_consumer_stages_must_match_canonical() -> None:
    ref = ArtifactRef(
        artifact_type="story",
        path="/tmp/s.yaml",
        producer_stage="intake",
        consumer_stages=("execution",),  # not story's canonical consumers
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = validate_stage_artifacts("intake", outputs=[ref])
    assert ISSUE_INCOMPATIBLE_CONSUMER in {i.code for i in result.errors}


def test_too_many_refs_for_exactly_one_slot() -> None:
    a = _valid_output_ref("story", "intake")
    b = _valid_output_ref("story", "intake")
    result = validate_stage_artifacts("intake", outputs=[a, b])
    slot = result.slots[0]
    assert slot.status is ArtifactValidationStatus.INVALID
    assert ISSUE_CARDINALITY_TOO_MANY in {i.code for i in slot.issues}


def test_one_or_more_accepts_multiple() -> None:
    inputs = [
        _valid_input_ref("story", "task-assignment"),
        _valid_input_ref("task_plan", "task-assignment"),
    ]
    a = _valid_output_ref("uow_spec", "task-assignment")
    b = _valid_output_ref("uow_spec", "task-assignment")
    assignment = _valid_output_ref("assignment", "task-assignment")
    result = validate_stage_artifacts(
        "task-assignment", inputs=inputs, outputs=[assignment, a, b]
    )
    assert result.ok, [i.to_dict() for i in result.errors]


# --------------------------------------------------------------------------- #
# Absent-metadata policy + path/URI preservation                             #
# --------------------------------------------------------------------------- #


def test_absent_metadata_is_warning_not_error() -> None:
    ref = ArtifactRef(artifact_type="story", path="/tmp/s.yaml")
    result = validate_stage_artifacts("task-generation", inputs=[ref])
    warn_codes = {i.code for i in result.warnings}
    assert WARNING_UNKNOWN_SCHEMA in warn_codes
    assert WARNING_UNKNOWN_PRODUCER in warn_codes
    assert WARNING_UNKNOWN_CONSUMER in warn_codes
    # Warnings never fail the story slot on their own; the story ref itself is OK.
    story_slot = next(s for s in result.slots if s.spec.artifact_type == "story")
    assert story_slot.status is ArtifactValidationStatus.VALID
    # But the required task_plan output is still missing -> not ok overall.
    assert not result.ok
    assert {i.code for i in result.errors} == {ISSUE_MISSING_REQUIRED}


def test_uri_only_ref_is_preserved_not_relabeled() -> None:
    ref = ArtifactRef(
        artifact_type="story",
        uri="gs://bucket/story.yaml",
        producer_stage=StoryArtifact.PRODUCER_STAGE,
        consumer_stages=tuple(StoryArtifact.CONSUMER_STAGES),
        artifact_schema=StoryArtifact.ARTIFACT_SCHEMA,
        artifact_schema_version=StoryArtifact.ARTIFACT_SCHEMA_VERSION,
    )
    result = validate_stage_artifacts("task-generation", inputs=[ref])
    story_slot = next(s for s in result.slots if s.spec.artifact_type == "story")
    outcome = story_slot.references[0]
    assert outcome.ref.uri == "gs://bucket/story.yaml"
    assert outcome.ref.path is None


def test_issue_locations_are_stable() -> None:
    result = validate_stage_artifacts("task-generation")
    story_slot = next(s for s in result.slots if s.spec.artifact_type == "story")
    assert story_slot.issues[0].location == "input.story"
    assert story_slot.issues[0].severity is ValidationSeverity.ERROR
