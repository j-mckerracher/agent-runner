"""Prompt 20 — implementation-report and QA-report payload contracts + loaders.

Covers `ImplementationReportPayload` and `QAReportPayload`: contract metadata,
structural validation with aggregated errors, the document-embedded
`schema_version` compatibility rules, the legacy `definition_of_done` alias,
extension-field preservation across a load/serialize round trip, the
`ArtifactRef` factory, non-mutating normalization, and strictly read-only
loading (including transport errors).
"""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError

import pytest

from artifacts import (
    ArtifactLoadError,
    ArtifactValidationError,
    ImplementationReportPayload,
    QAReportPayload,
    load_implementation_report,
    load_qa_report,
)


# --------------------------------------------------------------------------
# Valid payload builders (fresh dicts each call).
# --------------------------------------------------------------------------


def valid_impl_report() -> dict:
    return {
        "schema_version": "1",
        "uow_id": "UOW-1",
        "status": "complete",
        "implementation_summary": "Implemented the thing.",
        "definition_of_done_status": [
            {"item": "Tests pass", "met": True, "evidence": "pytest -q"},
            {"item": "Docs updated", "met": False, "evidence": "not yet"},
        ],
        "change_id": "C1",
    }


def valid_qa_report() -> dict:
    return {
        "schema_version": "1",
        "story_id": "C1",
        "qa_status": "pass",
        "acceptance_criteria_validation": {
            "AC1": {
                "status": "pass",
                "validation_method": "manual",
                "evidence": {"type": "log", "reference": "run.log"},
                "notes": "looks good",
            },
        },
        "final_recommendation": "approve",
    }


REPORT_BUILDERS = {
    ImplementationReportPayload: valid_impl_report,
    QAReportPayload: valid_qa_report,
}


# --------------------------------------------------------------------------
# Implementation report.
# --------------------------------------------------------------------------


def test_valid_current_implementation_report():
    report = ImplementationReportPayload.from_mapping(valid_impl_report())
    assert report.schema_version == "1"
    assert report.uow_id == "UOW-1"
    assert report.status == "complete"
    assert report.implementation_summary == "Implemented the thing."
    assert len(report.definition_of_done_status) == 2
    assert report.definition_of_done_status[0].item == "Tests pass"
    assert report.definition_of_done_status[0].met is True
    assert report.change_id == "C1"


def test_valid_legacy_report_with_no_schema_version():
    data = valid_impl_report()
    del data["schema_version"]
    report = ImplementationReportPayload.from_mapping(data)
    assert report.schema_version == "1"


def test_valid_legacy_definition_of_done_alias():
    data = valid_impl_report()
    data["definition_of_done"] = data.pop("definition_of_done_status")
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert len(report.definition_of_done_status) == 2
    assert [w.code for w in result.warnings] == ["legacy_definition_of_done"]


def test_canonical_serialization_uses_definition_of_done_status():
    data = valid_impl_report()
    data["definition_of_done"] = data.pop("definition_of_done_status")
    report = ImplementationReportPayload.from_mapping(data)
    rendered = report.to_dict()
    assert "definition_of_done_status" in rendered
    assert "definition_of_done" not in rendered


def test_missing_uow_id_is_error():
    data = valid_impl_report()
    del data["uow_id"]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_empty_uow_id_is_error():
    data = valid_impl_report()
    data["uow_id"] = "   "
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_invalid_implementation_status():
    data = valid_impl_report()
    data["status"] = "done"
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_missing_implementation_summary():
    data = valid_impl_report()
    del data["implementation_summary"]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_definition_of_done_is_not_a_list():
    data = valid_impl_report()
    data["definition_of_done_status"] = "done"
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_invalid_definition_of_done_entry_not_a_mapping():
    data = valid_impl_report()
    data["definition_of_done_status"] = ["not-a-mapping"]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "not_a_mapping" for e in exc_info.value.errors)


def test_invalid_definition_of_done_entry_missing_item():
    data = valid_impl_report()
    data["definition_of_done_status"] = [{"met": True}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_met_rejects_integer():
    data = valid_impl_report()
    data["definition_of_done_status"] = [{"item": "x", "met": 1}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_met_rejects_string_truthiness():
    data = valid_impl_report()
    data["definition_of_done_status"] = [{"item": "x", "met": "true"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_impl_report_unknown_extension_fields_are_preserved():
    data = valid_impl_report()
    data["files_modified"] = [{"path": "a.py", "change_type": "modified"}]
    data["no_mistakes_gate"] = {"required": True, "outcome": "passed"}
    report = ImplementationReportPayload.from_mapping(data)
    rendered = report.to_dict()
    assert rendered["files_modified"] == [{"path": "a.py", "change_type": "modified"}]
    assert rendered["no_mistakes_gate"] == {"required": True, "outcome": "passed"}


def test_impl_report_file_loader_success(tmp_path):
    path = tmp_path / "impl_report.yaml"
    path.write_text(
        "uow_id: UOW-1\n"
        "status: complete\n"
        "implementation_summary: did stuff\n"
        "definition_of_done_status:\n"
        "  - item: x\n"
        "    met: true\n",
        encoding="utf-8",
    )
    report = load_implementation_report(path)
    assert isinstance(report, ImplementationReportPayload)
    assert report.uow_id == "UOW-1"


def test_impl_report_missing_file_raises_load_error(tmp_path):
    with pytest.raises(ArtifactLoadError) as exc_info:
        load_implementation_report(tmp_path / "missing.yaml")
    assert "missing.yaml" in str(exc_info.value)


def test_impl_report_invalid_yaml_raises_load_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("key: [unterminated", encoding="utf-8")
    with pytest.raises(ArtifactLoadError):
        load_implementation_report(path)


def test_impl_report_non_mapping_top_level_raises_validation_error(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError) as exc_info:
        load_implementation_report(path)
    assert any(e.code == "not_a_mapping" for e in exc_info.value.errors)


def test_impl_report_unsupported_schema_version():
    data = valid_impl_report()
    data["schema_version"] = "2"
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_impl_report_boolean_schema_version_rejected():
    data = valid_impl_report()
    data["schema_version"] = True
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_impl_report_to_artifact_ref():
    report = ImplementationReportPayload.from_mapping(valid_impl_report())
    ref = report.to_artifact_ref(path="C1/execution/UOW-1/impl_report.yaml")
    assert ref.artifact_type == "impl_report"
    assert ref.producer_stage == "execution"
    assert ref.artifact_schema == "agent-workbench.impl-report"
    assert ref.artifact_schema_version == "1"
    assert ref.metadata["uow_id"] == "UOW-1"
    assert ref.metadata["status"] == "complete"
    assert ref.metadata["change_id"] == "C1"


def test_impl_report_to_artifact_ref_caller_metadata_wins():
    report = ImplementationReportPayload.from_mapping(valid_impl_report())
    ref = report.to_artifact_ref(path="p.yaml", metadata={"status": "overridden"})
    assert ref.metadata["status"] == "overridden"
    assert ref.metadata["uow_id"] == "UOW-1"


# --------------------------------------------------------------------------
# QA report.
# --------------------------------------------------------------------------


def test_valid_current_qa_report():
    report = QAReportPayload.from_mapping(valid_qa_report())
    assert report.schema_version == "1"
    assert report.story_id == "C1"
    assert report.qa_status == "pass"
    assert len(report.acceptance_criteria_validation) == 1
    entry = report.acceptance_criteria_validation[0]
    assert entry.ac_id == "AC1"
    assert entry.status == "pass"
    assert entry.evidence_type == "log"
    assert entry.evidence_reference == "run.log"
    assert report.final_recommendation == "approve"
    assert report.conditions == ()


def test_valid_legacy_qa_report_with_no_schema_version():
    data = valid_qa_report()
    del data["schema_version"]
    report = QAReportPayload.from_mapping(data)
    assert report.schema_version == "1"


def test_invalid_qa_status():
    data = valid_qa_report()
    data["qa_status"] = "unknown"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_invalid_ac_validation_status():
    data = valid_qa_report()
    data["acceptance_criteria_validation"]["AC1"]["status"] = "unknown"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_malformed_ac_validation_mapping_not_a_mapping():
    data = valid_qa_report()
    data["acceptance_criteria_validation"] = "AC1: pass"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_malformed_ac_validation_entry_not_a_mapping():
    data = valid_qa_report()
    data["acceptance_criteria_validation"]["AC1"] = "pass"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "not_a_mapping" for e in exc_info.value.errors)


def test_invalid_final_recommendation():
    data = valid_qa_report()
    data["final_recommendation"] = "maybe"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_approve_with_conditions_without_conditions():
    data = valid_qa_report()
    data["final_recommendation"] = "approve_with_conditions"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_approve_with_conditions_with_conditions_succeeds():
    data = valid_qa_report()
    data["final_recommendation"] = "approve_with_conditions"
    data["conditions"] = ["fix the docs"]
    report = QAReportPayload.from_mapping(data)
    assert report.conditions == ("fix the docs",)


def test_qa_report_unknown_extension_fields_are_preserved():
    data = valid_qa_report()
    data["knowledge_summary"] = "learned things"
    data["evidence_manifest"] = {"screenshots": ["s1.png"]}
    report = QAReportPayload.from_mapping(data)
    rendered = report.to_dict()
    assert rendered["knowledge_summary"] == "learned things"
    assert rendered["evidence_manifest"] == {"screenshots": ["s1.png"]}


def test_qa_report_file_loader_success(tmp_path):
    path = tmp_path / "qa_report.yaml"
    path.write_text(
        "story_id: C1\n"
        "qa_status: pass\n"
        "acceptance_criteria_validation:\n"
        "  AC1:\n"
        "    status: pass\n"
        "final_recommendation: approve\n",
        encoding="utf-8",
    )
    report = load_qa_report(path)
    assert isinstance(report, QAReportPayload)
    assert report.story_id == "C1"


def test_qa_report_missing_file_raises_load_error(tmp_path):
    with pytest.raises(ArtifactLoadError) as exc_info:
        load_qa_report(tmp_path / "missing.yaml")
    assert "missing.yaml" in str(exc_info.value)


def test_qa_report_invalid_yaml_raises_load_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("key: [unterminated", encoding="utf-8")
    with pytest.raises(ArtifactLoadError):
        load_qa_report(path)


def test_qa_report_unsupported_schema_version():
    data = valid_qa_report()
    data["schema_version"] = "2"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_qa_report_boolean_schema_version_rejected():
    data = valid_qa_report()
    data["schema_version"] = False
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "wrong_type" for e in exc_info.value.errors)


def test_qa_report_to_artifact_ref():
    report = QAReportPayload.from_mapping(valid_qa_report())
    ref = report.to_artifact_ref(path="C1/qa/qa_report.yaml")
    assert ref.artifact_type == "qa_report"
    assert ref.producer_stage == "qa"
    assert ref.artifact_schema == "agent-workbench.qa-report"
    assert ref.artifact_schema_version == "1"
    assert ref.metadata["story_id"] == "C1"
    assert ref.metadata["qa_status"] == "pass"
    assert ref.metadata["final_recommendation"] == "approve"


# --------------------------------------------------------------------------
# Shared contract behavior.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [ImplementationReportPayload, QAReportPayload])
def test_immutable(cls):
    report = cls.from_mapping(REPORT_BUILDERS[cls]())
    with pytest.raises(FrozenInstanceError):
        report.schema_version = "9"  # type: ignore[misc]


@pytest.mark.parametrize("cls", [ImplementationReportPayload, QAReportPayload])
def test_to_dict_returns_detached_mutable_builtins(cls):
    report = cls.from_mapping(REPORT_BUILDERS[cls]())
    rendered = report.to_dict()
    assert type(rendered) is dict
    rendered["mutated"] = True
    assert "mutated" not in cls.from_mapping(REPORT_BUILDERS[cls]()).to_dict()


@pytest.mark.parametrize("cls", [ImplementationReportPayload, QAReportPayload])
def test_caller_mutation_does_not_affect_stored_payload(cls):
    data = REPORT_BUILDERS[cls]()
    original = copy.deepcopy(data)
    report = cls.from_mapping(data)
    data["schema_version"] = "mutated"
    data["extra_injected"] = "mutated"
    assert cls.from_mapping(original).to_dict() == report.to_dict()


@pytest.mark.parametrize("cls", [ImplementationReportPayload, QAReportPayload])
def test_round_trip_mapping_behavior(cls):
    report = cls.from_mapping(REPORT_BUILDERS[cls]())
    round_tripped = cls.from_mapping(report.to_dict())
    assert round_tripped == report


def test_public_import_surface():
    import artifacts

    for name in (
        "ImplementationReportPayload",
        "QAReportPayload",
        "DefinitionOfDoneItem",
        "QAAcValidation",
        "load_implementation_report",
        "load_qa_report",
    ):
        assert hasattr(artifacts, name)
