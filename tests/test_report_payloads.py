"""Prompt 20 — implementation-report and QA-report payload contracts + loaders.

Covers `ImplementationReportArtifact` and `QAReportArtifact` (canonical names,
with `ImplementationReportPayload`/`QAReportPayload` kept as compatibility
aliases for the names introduced by an earlier, incomplete pass): contract
metadata, the mandatory typed sections (`files_modified`, `tests_written`,
`commands_executed`, `risks_identified` / `regression_risk_assessment`,
`issues_found`, `release_notes`, `evidence_manifest`), structural validation
with aggregated errors, the document-embedded `schema_version` compatibility
rules, every accepted legacy shape (each with its own stable warning code),
extension-field preservation across a load/serialize round trip for fields
that remain genuinely unmodeled, the `ArtifactRef` factory, non-mutating
normalization, and strictly read-only loading (including transport errors).
"""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError

import pytest

from artifacts import (
    ArtifactLoadError,
    ArtifactValidationError,
    CommandEntry,
    EvidenceManifest,
    FileChangeEntry,
    ImplementationReportArtifact,
    ImplementationReportPayload,
    IssueEntry,
    QAReportArtifact,
    QAReportPayload,
    RegressionRiskAssessment,
    ReleaseNotes,
    RiskEntry,
    TestEntry,
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
    assert report.files_modified == (FileChangeEntry(path="a.py", change_type="modified"),)
    rendered = report.to_dict()
    assert rendered["files_modified"] == [{"path": "a.py", "change_type": "modified"}]
    assert rendered["no_mistakes_gate"] == {"required": True, "outcome": "passed"}


def test_impl_report_files_modified_bare_string_entry():
    data = valid_impl_report()
    data["files_modified"] = ["a.py"]
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert [w.code for w in result.warnings] == ["legacy_file_path_string"]
    assert report.files_modified == (FileChangeEntry(path="a.py", change_type=None),)
    assert "change_type" not in report.to_dict()["files_modified"][0]


def test_impl_report_legacy_status_completed():
    data = valid_impl_report()
    data["status"] = "completed"
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.status == "complete"
    assert [w.code for w in result.warnings] == ["legacy_impl_status_completed"]


def test_impl_report_legacy_summary_alias():
    data = valid_impl_report()
    data["summary"] = data.pop("implementation_summary")
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.implementation_summary == "Implemented the thing."
    assert [w.code for w in result.warnings] == ["legacy_impl_summary"]


def test_impl_report_legacy_story_id_alias_for_change_id():
    data = valid_impl_report()
    del data["change_id"]
    data["story_id"] = "S1"
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.change_id == "S1"
    assert report.story_id == "S1"
    assert [w.code for w in result.warnings] == ["legacy_impl_story_id"]


def test_impl_report_legacy_files_changed_alias():
    data = valid_impl_report()
    data["files_changed"] = [{"path": "a.py", "change_type": "created"}]
    report, result = ImplementationReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert [w.code for w in result.warnings] == ["legacy_files_changed"]
    assert report.files_modified == (FileChangeEntry(path="a.py", change_type="created"),)


def test_impl_report_invalid_file_change_type():
    data = valid_impl_report()
    data["files_modified"] = [{"path": "a.py", "change_type": "renamed"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_impl_report_tests_written_reads_legacy_type_key():
    data = valid_impl_report()
    data["tests_written"] = [{"path": "t.py", "type": "unit", "cases_count": 3}]
    report = ImplementationReportPayload.from_mapping(data)
    assert report.tests_written == (TestEntry(path="t.py", test_type="unit", cases_count=3),)
    assert report.to_dict()["tests_written"][0]["type"] == "unit"


def test_impl_report_tests_written_missing_path_is_error():
    data = valid_impl_report()
    data["tests_written"] = [{"type": "unit"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_impl_report_commands_executed_valid_and_invalid_result():
    data = valid_impl_report()
    data["commands_executed"] = [{"command": "pytest -q", "result": "pass"}]
    report = ImplementationReportPayload.from_mapping(data)
    assert report.commands_executed == (CommandEntry(command="pytest -q", result="pass"),)

    data["commands_executed"] = [{"command": "pytest -q", "result": "ok"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_impl_report_risks_identified_reads_legacy_type_key():
    data = valid_impl_report()
    data["risks_identified"] = [{"type": "perf", "description": "slow path", "requires_escalation": True}]
    report = ImplementationReportPayload.from_mapping(data)
    assert report.risks_identified == (
        RiskEntry(risk_type="perf", description="slow path", requires_escalation=True),
    )
    assert report.to_dict()["risks_identified"][0]["type"] == "perf"


def test_impl_report_risk_missing_description_is_error():
    data = valid_impl_report()
    data["risks_identified"] = [{"type": "perf"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        ImplementationReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_impl_report_consumer_stages():
    assert ImplementationReportArtifact.CONSUMER_STAGES == ("execution", "qa", "pr-review")


def test_implementation_report_alias_identity():
    assert ImplementationReportPayload is ImplementationReportArtifact


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
    assert report.evidence_manifest == EvidenceManifest(screenshots=("s1.png",))
    rendered = report.to_dict()
    assert rendered["knowledge_summary"] == "learned things"
    assert rendered["evidence_manifest"] == {"screenshots": ["s1.png"]}


def test_qa_report_legacy_change_id_alias_for_story_id():
    data = valid_qa_report()
    del data["story_id"]
    data["change_id"] = "C1"
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.story_id == "C1"
    assert [w.code for w in result.warnings] == ["legacy_qa_change_id"]


@pytest.mark.parametrize("legacy_key", ["overall_result", "overall_status"])
def test_qa_report_legacy_overall_status_aliases(legacy_key):
    data = valid_qa_report()
    data[legacy_key] = data.pop("qa_status")
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.qa_status == "pass"
    assert [w.code for w in result.warnings] == ["legacy_qa_overall_status"]


def test_qa_report_legacy_conditional_pass_transform():
    data = valid_qa_report()
    del data["qa_status"]
    data["overall_status"] = "conditional_pass"
    del data["final_recommendation"]
    data["conditions"] = ["fix the docs"]
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.qa_status == "pass"
    assert report.final_recommendation == "approve_with_conditions"
    assert report.conditions == ("fix the docs",)
    codes = [w.code for w in result.warnings]
    assert "legacy_qa_overall_status" in codes
    assert "legacy_qa_conditional_pass" in codes


def test_qa_report_conditional_pass_without_conditions_still_errors():
    data = valid_qa_report()
    del data["qa_status"]
    data["overall_status"] = "conditional_pass"
    del data["final_recommendation"]
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "missing_field" for e in exc_info.value.errors)


def test_qa_report_conditional_pass_does_not_overwrite_explicit_recommendation():
    data = valid_qa_report()
    del data["qa_status"]
    data["overall_status"] = "conditional_pass"
    data["final_recommendation"] = "reject"
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.final_recommendation == "reject"


def test_qa_report_legacy_ac_validations_list_with_ac_id():
    data = valid_qa_report()
    del data["acceptance_criteria_validation"]
    data["ac_validations"] = [{"ac_id": "AC1", "status": "pass"}]
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.acceptance_criteria_validation[0].ac_id == "AC1"
    assert [w.code for w in result.warnings] == ["legacy_qa_ac_list"]


def test_qa_report_legacy_ac_validations_list_auto_keyed():
    data = valid_qa_report()
    del data["acceptance_criteria_validation"]
    data["ac_validations"] = [{"status": "pass"}, {"status": "fail"}]
    report = QAReportPayload.from_mapping(data)
    ac_ids = sorted(entry.ac_id for entry in report.acceptance_criteria_validation)
    assert ac_ids == ["AC1", "AC2"]


def test_qa_report_legacy_ac_validations_list_duplicate_id_is_error():
    data = valid_qa_report()
    del data["acceptance_criteria_validation"]
    data["ac_validations"] = [{"ac_id": "AC1", "status": "pass"}, {"ac_id": "AC1", "status": "fail"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_qa_report_ac_evidence_legacy_string():
    data = valid_qa_report()
    data["acceptance_criteria_validation"]["AC1"]["evidence"] = "run.log"
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    entry = report.acceptance_criteria_validation[0]
    assert entry.evidence_reference == "run.log"
    assert entry.evidence_references == ()
    assert [w.code for w in result.warnings] == ["legacy_qa_evidence_string"]


def test_qa_report_ac_evidence_legacy_list():
    data = valid_qa_report()
    data["acceptance_criteria_validation"]["AC1"]["evidence"] = ["run.log", "screenshot.png"]
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    entry = report.acceptance_criteria_validation[0]
    assert entry.evidence_references == ("run.log", "screenshot.png")
    assert [w.code for w in result.warnings] == ["legacy_qa_evidence_list"]


def test_qa_report_regression_risk_assessment_mapping_and_legacy_alias():
    data = valid_qa_report()
    data["regression_risk_assessment"] = {"overall_risk": "high"}
    report = QAReportPayload.from_mapping(data)
    assert report.regression_risk_assessment == RegressionRiskAssessment(overall_risk="high")

    data = valid_qa_report()
    data["regression_risk"] = "medium"
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.regression_risk_assessment == RegressionRiskAssessment(overall_risk="medium")
    assert [w.code for w in result.warnings] == ["legacy_qa_regression_risk"]


def test_qa_report_regression_risk_assessment_invalid_value():
    data = valid_qa_report()
    data["regression_risk_assessment"] = "extreme"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_qa_report_issues_found():
    data = valid_qa_report()
    data["issues_found"] = [{"issue_id": "I1", "severity": "high", "description": "broken"}]
    report = QAReportPayload.from_mapping(data)
    assert report.issues_found == (IssueEntry(issue_id="I1", severity="high", description="broken"),)


def test_qa_report_issues_found_invalid_severity():
    data = valid_qa_report()
    data["issues_found"] = [{"severity": "urgent"}]
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "invalid_value" for e in exc_info.value.errors)


def test_qa_report_release_notes_mapping_form():
    data = valid_qa_report()
    data["release_notes"] = {"summary": "Ships feature X."}
    report = QAReportPayload.from_mapping(data)
    assert report.release_notes == ReleaseNotes(summary="Ships feature X.")


def test_qa_report_release_notes_legacy_string_form():
    data = valid_qa_report()
    data["release_notes"] = "Ships feature X."
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.release_notes == ReleaseNotes(summary="Ships feature X.")
    assert [w.code for w in result.warnings] == ["legacy_qa_release_notes_string"]


def test_qa_report_release_notes_legacy_list_form():
    data = valid_qa_report()
    data["release_notes"] = ["Added X", "Fixed Y"]
    report, result = QAReportPayload.from_mapping_with_validation(data)
    assert result.ok
    assert report.release_notes == ReleaseNotes(raw_items=("Added X", "Fixed Y"))
    assert [w.code for w in result.warnings] == ["legacy_qa_release_notes_list"]


def test_qa_report_evidence_manifest_valid():
    data = valid_qa_report()
    data["evidence_manifest"] = {"screenshots": ["a.png"], "videos": ["b.mp4"], "logs": ["c.log"]}
    report = QAReportPayload.from_mapping(data)
    assert report.evidence_manifest == EvidenceManifest(
        screenshots=("a.png",), videos=("b.mp4",), logs=("c.log",)
    )


def test_qa_report_evidence_manifest_not_a_mapping_is_error():
    data = valid_qa_report()
    data["evidence_manifest"] = "logs.txt"
    with pytest.raises(ArtifactValidationError) as exc_info:
        QAReportPayload.from_mapping(data)
    assert any(e.code == "not_a_mapping" for e in exc_info.value.errors)


def test_qa_report_consumer_stages():
    assert QAReportArtifact.CONSUMER_STAGES == ("qa", "pr-review")


def test_qa_report_alias_identity():
    assert QAReportPayload is QAReportArtifact


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
        "ImplementationReportArtifact",
        "ImplementationReportPayload",
        "QAReportArtifact",
        "QAReportPayload",
        "REPORT_ARTIFACTS",
        "DefinitionOfDoneItem",
        "QAAcValidation",
        "FileChangeEntry",
        "TestEntry",
        "CommandEntry",
        "RiskEntry",
        "RegressionRiskAssessment",
        "IssueEntry",
        "ReleaseNotes",
        "EvidenceManifest",
        "load_implementation_report",
        "load_qa_report",
    ):
        assert hasattr(artifacts, name)
