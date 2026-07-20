"""Prompt 19 — planning-artifact payload contracts + read-only loaders.

Covers the four payload contracts (`StoryArtifact`, `TaskPlanArtifact`,
`AssignmentArtifact`, `UowSpecArtifact`): contract metadata, structural
validation with aggregated errors, accepted-legacy-shape warnings observable via
the `*_with_validation` API, core-field-projection serialization, the
`ArtifactRef` factory, non-mutating normalization, and strictly read-only
loading (including transport errors and a simulated missing PyYAML).
"""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from artifacts import (
    ArtifactLoadError,
    ArtifactRef,
    ArtifactRefValidationError,
    ArtifactValidationError,
    ArtifactValidationStatus,
    AssignmentArtifact,
    PLANNING_ARTIFACTS,
    StoryArtifact,
    TaskPlanArtifact,
    UowSpecArtifact,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from artifacts import payloads as payloads_mod


# --------------------------------------------------------------------------
# Valid payload builders (fresh dicts each call).
# --------------------------------------------------------------------------


def valid_story() -> dict:
    return {
        "change_id": "C1",
        "title": "A story",
        "description": "Do the thing.",
        "acceptance_criteria": {"AC1": "first", "AC2": "second"},
    }


def valid_task_plan() -> dict:
    return {
        "story_id": "C1",
        "tasks": [
            {"id": "T1", "title": "task one", "ac_mapping": ["AC1"]},
            {"id": "T2", "title": "task two", "ac_mapping": ["AC2"], "dependencies": ["T1"]},
        ],
    }


def valid_assignment() -> dict:
    return {
        "story_id": "C1",
        "batches": [
            {
                "batch_id": 1,
                "parallel_execution": True,
                "uows": [
                    {"uow_id": "U1", "source_task_id": "T1", "assigned_role": "software-engineer"}
                ],
            }
        ],
    }


def valid_uow_spec() -> dict:
    return {
        "uow_id": "U1",
        "source_task_id": "T1",
        "change_id": "C1",
        "story_id": "C1",
        "assigned_role": "software-engineer",
        "title": "unit of work",
    }


BUILDERS = {
    StoryArtifact: valid_story,
    TaskPlanArtifact: valid_task_plan,
    AssignmentArtifact: valid_assignment,
    UowSpecArtifact: valid_uow_spec,
}


# --------------------------------------------------------------------------
# Public surface + contract metadata.
# --------------------------------------------------------------------------


def test_planning_artifacts_registry_order():
    assert PLANNING_ARTIFACTS == (
        StoryArtifact,
        TaskPlanArtifact,
        AssignmentArtifact,
        UowSpecArtifact,
    )


def test_artifact_types_and_schemas_unique():
    types = [cls.ARTIFACT_TYPE for cls in PLANNING_ARTIFACTS]
    schemas = [cls.ARTIFACT_SCHEMA for cls in PLANNING_ARTIFACTS]
    assert len(set(types)) == len(types)
    assert len(set(schemas)) == len(schemas)


@pytest.mark.parametrize(
    "cls, artifact_type, schema, producer, template, params, fmt",
    [
        (StoryArtifact, "story", "agent-workbench.story", "intake", "{change_id}/intake/story.yaml", ("change_id",), "yaml"),
        (TaskPlanArtifact, "task_plan", "agent-workbench.task-plan", "task-generation", "{change_id}/planning/tasks.yaml", ("change_id",), "yaml"),
        (AssignmentArtifact, "assignment", "agent-workbench.assignment", "task-assignment", "{change_id}/planning/assignments.json", ("change_id",), "json"),
        (UowSpecArtifact, "uow_spec", "agent-workbench.uow-spec", "task-assignment", "{change_id}/execution/{uow_id}/uow_spec.yaml", ("change_id", "uow_id"), "yaml"),
    ],
)
def test_contract_metadata(cls, artifact_type, schema, producer, template, params, fmt):
    assert cls.ARTIFACT_TYPE == artifact_type
    assert cls.ARTIFACT_SCHEMA == schema
    assert cls.ARTIFACT_SCHEMA_VERSION == "1"
    assert cls.PRODUCER_STAGE == producer
    assert cls.PATH_SCOPE == "agent_context"
    assert cls.RELATIVE_PATH_TEMPLATE == template
    assert cls.PATH_PARAMETERS == params
    assert cls.CONTENT_FORMAT == fmt


def test_consumer_stages():
    assert StoryArtifact.CONSUMER_STAGES == ("task-generation", "task-assignment", "qa", "pr-review")
    assert TaskPlanArtifact.CONSUMER_STAGES == ("task-assignment", "qa", "pr-review")
    assert AssignmentArtifact.CONSUMER_STAGES == ("execution", "qa", "pr-review")
    assert UowSpecArtifact.CONSUMER_STAGES == ("execution",)


def test_contract_constants_are_not_dataclass_fields():
    # ClassVar metadata must not leak into the payload fields (fields() excludes
    # ClassVar pseudo-entries; __dataclass_fields__ would still list them).
    from dataclasses import fields

    field_names = {f.name for f in fields(StoryArtifact)}
    assert "ARTIFACT_TYPE" not in field_names
    assert "CONSUMER_STAGES" not in field_names
    assert field_names == {"change_id", "title", "description", "acceptance_criteria"}


# --------------------------------------------------------------------------
# Valid construction + immutability.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", list(BUILDERS))
def test_from_mapping_valid(cls):
    obj = cls.from_mapping(BUILDERS[cls]())
    assert isinstance(obj, cls)


def test_story_fields_normalized():
    story = StoryArtifact.from_mapping(valid_story())
    assert story.change_id == "C1"
    assert [ac.ac_id for ac in story.acceptance_criteria] == ["AC1", "AC2"]
    assert story.acceptance_criteria[0].text == "first"


def test_task_plan_fields():
    plan = TaskPlanArtifact.from_mapping(valid_task_plan())
    assert plan.story_id == "C1"
    assert [t.id for t in plan.tasks] == ["T1", "T2"]
    assert plan.tasks[1].dependencies == ("T1",)


def test_assignment_fields():
    assignment = AssignmentArtifact.from_mapping(valid_assignment())
    assert assignment.batches[0].batch_id == 1
    assert assignment.batches[0].parallel_execution is True
    assert assignment.batches[0].uows[0].uow_id == "U1"


@pytest.mark.parametrize("cls", list(BUILDERS))
def test_immutable(cls):
    obj = cls.from_mapping(BUILDERS[cls]())
    with pytest.raises(FrozenInstanceError):
        obj.__setattr__("title", "mutated")


# --------------------------------------------------------------------------
# Structural validation + aggregated errors.
# --------------------------------------------------------------------------


def test_validate_payload_returns_result():
    result = StoryArtifact.validate_payload(valid_story())
    assert isinstance(result, ValidationResult)
    assert result.ok
    assert result.errors == ()


def test_missing_required_is_domain_error_not_type_error():
    with pytest.raises(ArtifactValidationError):
        StoryArtifact.from_mapping({})


def test_aggregated_errors():
    # Empty story reports several missing-field errors at once.
    result = StoryArtifact.validate_payload({})
    codes = [issue.code for issue in result.errors]
    assert codes.count("missing_field") >= 3  # change_id, title, description, acceptance_criteria
    assert not result.ok


def test_non_mapping_payload_reports_not_a_mapping():
    result = TaskPlanArtifact.validate_payload(["not", "a", "mapping"])
    assert [i.code for i in result.errors] == ["not_a_mapping"]


def test_empty_collection_error():
    result = TaskPlanArtifact.validate_payload({"tasks": []})
    assert any(i.code == "empty_collection" for i in result.errors)


def test_wrong_type_error():
    result = AssignmentArtifact.validate_payload({"batches": "nope"})
    assert any(i.code == "wrong_type" for i in result.errors)


def test_story_ac_mapping_rejects_non_string_value():
    # Non-string AC values must be rejected, not silently coerced via str().
    result = StoryArtifact.validate_payload(
        {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": {"AC1": 123}}
    )
    assert any(i.code == "wrong_type" for i in result.errors)
    assert not result.ok


def test_story_ac_mapping_rejects_non_string_key():
    result = StoryArtifact.validate_payload(
        {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": {123: "text"}}
    )
    assert any(i.code == "wrong_type" for i in result.errors)


def test_story_ac_mapping_rejects_empty_value():
    result = StoryArtifact.validate_payload(
        {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": {"AC1": "   "}}
    )
    assert any(i.code == "missing_field" for i in result.errors)


def test_story_ac_list_rejects_non_string_item():
    # Legacy list shape still warns, but a non-string item is still an error.
    result = StoryArtifact.validate_payload(
        {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": ["ok", 123]}
    )
    assert any(i.code == "wrong_type" for i in result.errors)
    assert {w.code for w in result.warnings} == {"legacy_ac_list"}


def test_task_plan_rejects_invalid_priority():
    result = TaskPlanArtifact.validate_payload(
        {"tasks": [{"id": "T1", "title": "t", "ac_mapping": ["AC1"], "priority": "urgent"}]}
    )
    assert any(i.code == "invalid_value" for i in result.errors)


def test_task_plan_rejects_invalid_complexity():
    result = TaskPlanArtifact.validate_payload(
        {"tasks": [{"id": "T1", "title": "t", "ac_mapping": ["AC1"], "complexity": "impossible"}]}
    )
    assert any(i.code == "invalid_value" for i in result.errors)


def test_task_plan_accepts_valid_priority_and_complexity():
    plan = TaskPlanArtifact.from_mapping(
        {
            "tasks": [
                {
                    "id": "T1",
                    "title": "t",
                    "ac_mapping": ["AC1"],
                    "priority": "high",
                    "complexity": "complex",
                }
            ]
        }
    )
    assert plan.tasks[0].priority == "high"
    assert plan.tasks[0].complexity == "complex"


def test_validation_error_message_renders_codes():
    try:
        StoryArtifact.from_mapping({})
    except ArtifactValidationError as exc:
        assert "missing_field:" in str(exc)
        assert all(i.severity is ValidationSeverity.ERROR for i in exc.errors)
    else:  # pragma: no cover
        pytest.fail("expected ArtifactValidationError")


# --------------------------------------------------------------------------
# Compatibility warnings (observed via *_with_validation).
# --------------------------------------------------------------------------


def test_story_legacy_ac_list_warns_and_normalizes():
    story, result = StoryArtifact.from_mapping_with_validation(
        {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": ["x", "y"]}
    )
    assert result.ok
    assert {w.code for w in result.warnings} == {"legacy_ac_list"}
    assert [ac.ac_id for ac in story.acceptance_criteria] == ["AC1", "AC2"]


def test_assignment_legacy_shapes_warn():
    _, result = AssignmentArtifact.from_mapping_with_validation(
        {"execution_schedule": [{"batch": 3, "uows": [{"uow_id": "U1", "source_task_id": "T1"}]}]}
    )
    assert result.ok
    codes = {w.code for w in result.warnings}
    assert "legacy_execution_schedule" in codes
    assert "legacy_batch_key" in codes


def test_task_plan_legacy_aliases_warn():
    plan, result = TaskPlanArtifact.from_mapping_with_validation(
        {
            "tasks": [
                {
                    "task_id": "T1",
                    "title": "t",
                    "acceptance_criteria_mapped": ["AC1"],
                    "estimated_complexity": "simple",
                }
            ]
        }
    )
    assert result.ok
    codes = {w.code for w in result.warnings}
    assert codes == {"legacy_task_id", "legacy_acceptance_criteria_mapped", "legacy_estimated_complexity"}
    assert plan.tasks[0].id == "T1"
    assert plan.tasks[0].ac_mapping == ("AC1",)
    assert plan.tasks[0].complexity == "simple"


def test_partial_uow_spec_warns():
    spec, result = UowSpecArtifact.from_mapping_with_validation({"uow_id": "U1", "title": "t"})
    assert result.ok
    assert {w.code for w in result.warnings} == {"legacy_partial_uow_spec"}
    assert spec.uow_id == "U1"
    assert spec.source_task_id is None


def test_from_mapping_discards_warnings_but_succeeds():
    # Convenience wrapper still returns an instance for warning-only payloads.
    spec = UowSpecArtifact.from_mapping({"uow_id": "U1"})
    assert spec.uow_id == "U1"


# --------------------------------------------------------------------------
# Field fidelity + core-field-projection semantics.
# --------------------------------------------------------------------------


def test_uow_entry_preserves_role_priority_rationale():
    assignment = AssignmentArtifact.from_mapping(
        {
            "batches": [
                {
                    "batch_id": 1,
                    "uows": [
                        {
                            "uow_id": "U1",
                            "source_task_id": "T1",
                            "assigned_role": "qa-engineer",
                            "priority_in_batch": 2,
                            "rationale": "because",
                        }
                    ],
                }
            ]
        }
    )
    uow = assignment.batches[0].uows[0]
    assert uow.assigned_role == "qa-engineer"
    assert uow.priority_in_batch == 2
    assert uow.rationale == "because"


def test_unknown_top_level_fields_ignored():
    payload = valid_story()
    payload["ac_coverage_matrix"] = {"AC1": ["T1"]}
    payload["notes"] = "extra"
    story = StoryArtifact.from_mapping(payload)  # no error
    assert "notes" not in story.to_dict()  # projection, not lossless mirror


@pytest.mark.parametrize("cls", list(BUILDERS))
def test_model_dict_round_trip(cls):
    obj = cls.from_mapping(BUILDERS[cls]())
    again = cls.from_mapping(obj.to_dict())
    assert obj == again


@pytest.mark.parametrize("cls", list(BUILDERS))
def test_deterministic_json(cls):
    obj = cls.from_mapping(BUILDERS[cls]())
    assert obj.to_json() == obj.to_json()
    assert obj.to_json() == json.dumps(obj.to_dict(), sort_keys=True)


# --------------------------------------------------------------------------
# Non-mutating normalization.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls, payload",
    [
        (StoryArtifact, {"change_id": "C1", "title": "T", "description": "D", "acceptance_criteria": ["a", "b"]}),
        (TaskPlanArtifact, {"tasks": [{"task_id": "T1", "title": "t", "acceptance_criteria_mapped": ["AC1"]}]}),
        (AssignmentArtifact, {"execution_schedule": [{"batch": 1, "uows": [{"uow_id": "U1", "source_task_id": "T1"}]}]}),
    ],
)
def test_normalization_does_not_mutate_caller(cls, payload):
    snapshot = copy.deepcopy(payload)
    cls.from_mapping(payload)
    assert payload == snapshot


# --------------------------------------------------------------------------
# ArtifactRef factory.
# --------------------------------------------------------------------------


def test_to_artifact_ref_default_no_metadata():
    story = StoryArtifact.from_mapping(valid_story())
    ref = story.to_artifact_ref(path="C1/intake/story.yaml")
    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_type == "story"
    assert ref.producer_stage == "intake"
    assert ref.consumer_stages == ("task-generation", "task-assignment", "qa", "pr-review")
    assert ref.artifact_schema == "agent-workbench.story"
    assert ref.artifact_schema_version == "1"
    assert ref.path == Path("C1/intake/story.yaml")
    assert ref.uri is None
    assert ref.validation_status is None
    assert dict(ref.metadata) == {}


def test_to_artifact_ref_uri_only():
    story = StoryArtifact.from_mapping(valid_story())
    ref = story.to_artifact_ref(uri="s3://bucket/story.yaml")
    assert ref.uri == "s3://bucket/story.yaml"
    assert ref.path is None


def test_to_artifact_ref_requires_path_or_uri():
    story = StoryArtifact.from_mapping(valid_story())
    with pytest.raises(ArtifactRefValidationError):
        story.to_artifact_ref()


def test_to_artifact_ref_preserves_status_and_checksum():
    plan = TaskPlanArtifact.from_mapping(valid_task_plan())
    ref = plan.to_artifact_ref(
        path="C1/planning/tasks.yaml",
        validation_status=ArtifactValidationStatus.VALID,
        checksum_sha256="a" * 64,
    )
    assert ref.validation_status is ArtifactValidationStatus.VALID
    assert ref.checksum_sha256 == "a" * 64


def test_to_artifact_ref_metadata_defensively_handled():
    story = StoryArtifact.from_mapping(valid_story())
    md = {"k": "v"}
    ref = story.to_artifact_ref(path="p", metadata=md)
    md["k"] = "mutated"
    assert ref.metadata["k"] == "v"


def test_to_artifact_ref_no_filesystem_existence_check():
    spec = UowSpecArtifact.from_mapping(valid_uow_spec())
    ref = spec.to_artifact_ref(path="/does/not/exist/uow_spec.yaml")
    assert ref.artifact_type == "uow_spec"


# --------------------------------------------------------------------------
# Read-only loading.
# --------------------------------------------------------------------------


def _write(path: Path, payload: dict) -> None:
    # JSON is valid YAML, so a JSON body loads under both formats — keeps the
    # fixture free of hand-indented YAML.
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "cls, name, builder",
    [
        (StoryArtifact, "story.yaml", valid_story),
        (TaskPlanArtifact, "tasks.yaml", valid_task_plan),
        (AssignmentArtifact, "assignments.json", valid_assignment),
        (UowSpecArtifact, "uow_spec.yaml", valid_uow_spec),
    ],
)
def test_load_is_read_only(cls, name, builder, tmp_path, monkeypatch):
    src = tmp_path / name
    _write(src, builder())
    before_bytes = src.read_bytes()
    before_mtime = src.stat().st_mtime_ns

    def _boom(*args, **kwargs):
        raise AssertionError("load must not write to the source")

    monkeypatch.setattr(Path, "write_text", _boom)
    monkeypatch.setattr(Path, "write_bytes", _boom)
    monkeypatch.setattr(Path, "touch", _boom)

    obj, result = cls.load_with_validation(src)
    assert isinstance(obj, cls)
    assert result.ok
    assert src.read_bytes() == before_bytes
    assert src.stat().st_mtime_ns == before_mtime


def test_load_surfaces_legacy_warnings(tmp_path):
    src = tmp_path / "assignments.json"
    _write(src, {"execution_schedule": [{"batch": 1, "uows": [{"uow_id": "U1", "source_task_id": "T1"}]}]})
    _, result = AssignmentArtifact.load_with_validation(src)
    assert {w.code for w in result.warnings} >= {"legacy_execution_schedule", "legacy_batch_key"}


def test_load_convenience_returns_instance(tmp_path):
    src = tmp_path / "story.yaml"
    _write(src, valid_story())
    story = StoryArtifact.load(src)
    assert story.change_id == "C1"


def test_load_missing_file_raises_load_error(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ArtifactLoadError) as exc:
        AssignmentArtifact.load(missing)
    assert str(missing) in str(exc.value)


def test_load_malformed_json_raises_load_error(tmp_path):
    src = tmp_path / "assignments.json"
    src.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ArtifactLoadError):
        AssignmentArtifact.load(src)


def test_load_malformed_yaml_raises_load_error(tmp_path):
    src = tmp_path / "story.yaml"
    src.write_text("foo: [1, 2", encoding="utf-8")
    with pytest.raises(ArtifactLoadError):
        StoryArtifact.load(src)


def test_load_missing_pyyaml_raises_load_error(tmp_path, monkeypatch):
    src = tmp_path / "story.yaml"
    _write(src, valid_story())

    def _no_yaml():
        raise ModuleNotFoundError("No module named 'yaml'")

    monkeypatch.setattr(payloads_mod, "_import_yaml", _no_yaml)
    with pytest.raises(ArtifactLoadError) as exc:
        StoryArtifact.load(src)
    assert "PyYAML" in str(exc.value)


def test_structurally_invalid_file_raises_validation_error(tmp_path):
    # A readable-but-invalid payload is a validation error, not a load error.
    src = tmp_path / "tasks.yaml"
    _write(src, {"tasks": []})
    with pytest.raises(ArtifactValidationError):
        TaskPlanArtifact.load(src)


# --------------------------------------------------------------------------
# ValidationResult.issues defensive tuple coercion.
# --------------------------------------------------------------------------


def test_validation_result_coerces_list_to_tuple():
    issue = ValidationIssue(code="missing_field", message="x", severity=ValidationSeverity.ERROR)
    result = ValidationResult(issues=[issue])
    assert isinstance(result.issues, tuple)


def test_validation_result_immune_to_later_mutation_of_source_list():
    issue = ValidationIssue(code="missing_field", message="x", severity=ValidationSeverity.ERROR)
    source = [issue]
    result = ValidationResult(issues=source)
    source.append(issue)
    source.clear()
    assert len(result.issues) == 1
    assert result.issues == (issue,)
