"""Prompt 23 — deterministic tests for live workflow artifact adoption.

Exercises `workflow.live_artifacts.LiveArtifactCollector` against real
fixture files on disk (real `RELATIVE_PATH_TEMPLATE`s, typed loaders) and a
local recording `EventSink` double — no real LLM, network, server, or Opik
calls. Also proves `WorkflowRunner` propagation (patched `run.main`) and,
for A3/failure-precedence, real `run.main` wiring with stage agents and
optimizer loops replaced by fakes (same harness as
`tests/test_run_worktree_integration.py`).

Plan-item coverage (see `docs/stage-artifact-contracts.md` /
`you-must-use-the-lively-goblet.md` Prompt 23 plan): items 1-17, 19-23.
Item 18 lives in `tests/test_stage_artifact_contracts.py`; item 24 lives in
`tests/test_workflow_models.py` — both already landed.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from artifacts import ArtifactValidationStatus
from core.workflow_constants import (
    STAGE_EXECUTION,
    STAGE_INTAKE,
    STAGE_PR_REVIEW,
    STAGE_QA,
    STAGE_TASK_ASSIGNMENT,
    STAGE_TASK_GENERATION,
)
from workflow.live_artifacts import LiveArtifactCollector
from workflow.models import RunSpec, RunStatus
from workflow.runner import WorkflowRunner

import run
from tests.test_run_worktree_integration import (
    _base_patches,
    _install_fake_workflow_modules,
    _make_mock_lock,
    _workflow_input,
)


# ---------------------------------------------------------------------------
# Fixture-writing helpers (minimal valid payload dicts, real path templates).
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_story(root: Path, change_id: str = "C1") -> None:
    _write_yaml(root / "intake/story.yaml", {
        "change_id": change_id,
        "title": "A story",
        "description": "Do the thing.",
        "acceptance_criteria": {"AC1": "first", "AC2": "second"},
    })


def _write_task_plan(root: Path) -> None:
    _write_yaml(root / "planning/tasks.yaml", {
        "story_id": "C1",
        "tasks": [
            {"id": "T1", "title": "task one", "ac_mapping": ["AC1"]},
            {"id": "T2", "title": "task two", "ac_mapping": ["AC2"]},
        ],
    })


def _write_assignment(root: Path, uow_ids: tuple[str, ...] = ("U1", "U2")) -> None:
    _write_json(root / "planning/assignments.json", {
        "story_id": "C1",
        "batches": [
            {
                "batch_id": 1,
                "parallel_execution": True,
                "uows": [
                    {"uow_id": uid, "source_task_id": f"T{i + 1}", "assigned_role": "software-engineer"}
                    for i, uid in enumerate(uow_ids)
                ],
            }
        ],
    })


def _write_uow_spec(root: Path, uow_id: str) -> None:
    _write_yaml(root / f"execution/{uow_id}/uow_spec.yaml", {
        "uow_id": uow_id,
        "source_task_id": "T1",
        "change_id": "C1",
        "story_id": "C1",
        "assigned_role": "software-engineer",
        "title": "unit of work",
    })


def _write_impl_report(root: Path, uow_id: str) -> None:
    _write_yaml(root / f"execution/{uow_id}/impl_report.yaml", {
        "schema_version": "1",
        "uow_id": uow_id,
        "status": "complete",
        "implementation_summary": "Implemented the thing.",
        "definition_of_done_status": [
            {"item": "Tests pass", "met": True, "evidence": "pytest -q"},
        ],
        "change_id": "C1",
    })


def _write_qa_report(root: Path) -> None:
    _write_yaml(root / "qa/qa_report.yaml", {
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
    })


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


# --------------------------------------------------------------------------- #
# 1. Full multi-stage flow                                                    #
# --------------------------------------------------------------------------- #


def test_full_flow_discovers_all_stage_artifacts(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)

    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)

    collector.emit_stage_inputs(STAGE_TASK_GENERATION)
    _write_task_plan(tmp_path)
    collector.register_stage_outputs(STAGE_TASK_GENERATION)

    collector.emit_stage_inputs(STAGE_TASK_ASSIGNMENT)
    _write_assignment(tmp_path, ("U1", "U2"))
    _write_uow_spec(tmp_path, "U1")
    _write_uow_spec(tmp_path, "U2")
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)

    collector.emit_stage_inputs(STAGE_EXECUTION)
    _write_impl_report(tmp_path, "U1")
    _write_impl_report(tmp_path, "U2")
    collector.register_stage_outputs(STAGE_EXECUTION)

    collector.emit_stage_inputs(STAGE_QA)
    _write_qa_report(tmp_path)
    collector.register_stage_outputs(STAGE_QA)

    collector.emit_stage_inputs(STAGE_PR_REVIEW)

    refs = collector.references
    assert [r.artifact_type for r in refs] == [
        "story", "task_plan", "assignment", "uow_spec", "uow_spec",
        "impl_report", "impl_report", "qa_report",
    ]
    assert all(r.validation_status is ArtifactValidationStatus.VALID for r in refs)


# --------------------------------------------------------------------------- #
# 2. Input/output lifecycle event ordering per stage                         #
# --------------------------------------------------------------------------- #


def test_input_then_output_event_ordering_per_stage(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)

    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)
    assert _types(sink) == ["artifact.created", "artifact.validated"]

    collector.emit_stage_inputs(STAGE_TASK_GENERATION)
    _write_task_plan(tmp_path)
    collector.register_stage_outputs(STAGE_TASK_GENERATION)
    # Input (story) is terminal-only; output (task_plan) is created+terminal.
    assert _types(sink)[2:] == ["artifact.validated", "artifact.created", "artifact.validated"]


def test_emit_stage_inputs_is_idempotent_per_stage(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)

    collector.emit_stage_inputs(STAGE_TASK_GENERATION)
    collector.emit_stage_inputs(STAGE_TASK_GENERATION)  # second call: no-op
    story_events = [e for e in sink.events if e.metadata.get("artifact_type") == "story"]
    # created+validated (output) + validated (single input emission) = 3, not 4.
    assert len(story_events) == 3


# --------------------------------------------------------------------------- #
# 3-4. Valid vs malformed present output                                     #
# --------------------------------------------------------------------------- #


def test_valid_output_emits_created_then_validated(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)
    assert _types(sink) == ["artifact.created", "artifact.validated"]
    assert collector.references[0].validation_status is ArtifactValidationStatus.VALID


def test_malformed_present_output_emits_created_then_invalid(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_yaml(tmp_path / "intake/story.yaml", {"title": "missing required fields"})
    collector.register_stage_outputs(STAGE_INTAKE)
    assert _types(sink) == ["artifact.created", "artifact.invalid"]
    assert collector.references[0].validation_status is ArtifactValidationStatus.INVALID


# --------------------------------------------------------------------------- #
# 5-6. Missing vs malformed are distinct                                     #
# --------------------------------------------------------------------------- #


def test_missing_required_output_emits_missing_no_fabricated_created(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)  # story.yaml never written
    assert _types(sink) == ["artifact.missing"]
    assert collector.references == ()


def test_present_malformed_is_invalid_not_missing(tmp_path):
    present_root = tmp_path / "present"
    absent_root = tmp_path / "absent"
    sink_present, sink_absent = _RecordingSink(), _RecordingSink()

    present = LiveArtifactCollector(run_id="run-1", sink=sink_present)
    present.bind(present_root)
    _write_yaml(present_root / "intake/story.yaml", {"title": "no required fields"})
    present.register_stage_outputs(STAGE_INTAKE)

    absent = LiveArtifactCollector(run_id="run-2", sink=sink_absent)
    absent.bind(absent_root)
    absent.register_stage_outputs(STAGE_INTAKE)

    assert _types(sink_present) == ["artifact.created", "artifact.invalid"]
    assert _types(sink_absent) == ["artifact.missing"]
    assert len(present.references) == 1
    assert absent.references == ()


# --------------------------------------------------------------------------- #
# 7. Accepted legacy shape                                                    #
# --------------------------------------------------------------------------- #


def test_accepted_legacy_assignment_shape_validates_with_warning_codes(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_json(tmp_path / "planning/assignments.json", {
        "execution_schedule": [
            {"batch": 1, "uows": [{"uow_id": "U1", "source_task_id": "T1"}]},
        ],
    })
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)

    assignment_ref = next(r for r in collector.references if r.artifact_type == "assignment")
    assert assignment_ref.validation_status is ArtifactValidationStatus.VALID
    codes = assignment_ref.metadata.get("validation_issue_codes", [])
    assert "legacy_execution_schedule" in codes
    assert "legacy_batch_key" in codes


# --------------------------------------------------------------------------- #
# 8. Deterministic multi-UoW ordering                                        #
# --------------------------------------------------------------------------- #


def test_multi_uow_ordering_follows_assignment_not_filesystem(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_assignment(tmp_path, ("U1", "U2"))
    # Write on disk in reverse order to prove ordering tracks the assignment,
    # not directory-listing/filesystem order.
    _write_uow_spec(tmp_path, "U2")
    _write_uow_spec(tmp_path, "U1")
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)

    uow_paths = [str(r.path) for r in collector.references if r.artifact_type == "uow_spec"]
    assert uow_paths == [
        str(tmp_path / "execution/U1/uow_spec.yaml"),
        str(tmp_path / "execution/U2/uow_spec.yaml"),
    ]


# --------------------------------------------------------------------------- #
# 9. Dedup on repeated registration                                          #
# --------------------------------------------------------------------------- #


def test_register_stage_outputs_dedups_on_repeat_call(tmp_path):
    collector = LiveArtifactCollector(run_id="run-1", sink=None)
    collector.bind(tmp_path)
    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)
    collector.register_stage_outputs(STAGE_INTAKE)
    assert len(collector.references) == 1


# --------------------------------------------------------------------------- #
# 10-13. WorkflowRunner propagation (patched `run.main`)                     #
# --------------------------------------------------------------------------- #


def test_default_adapter_populates_context_and_result_with_real_refs(tmp_path):
    def fake_main(**kwargs):
        collector = kwargs["artifact_collector"]
        collector.bind(tmp_path)
        _write_story(tmp_path)
        collector.register_stage_outputs(STAGE_INTAKE)
        return "ok"

    runner = WorkflowRunner()
    with patch("run.main", side_effect=fake_main):
        result = runner.run(RunSpec())

    assert result.status is RunStatus.SUCCEEDED
    assert len(result.artifact_references) == 1
    assert result.artifact_references[0].artifact_type == "story"


def test_run_capturing_retains_refs_registered_before_failure(tmp_path):
    def fake_main(**kwargs):
        collector = kwargs["artifact_collector"]
        collector.bind(tmp_path)
        _write_story(tmp_path)
        collector.register_stage_outputs(STAGE_INTAKE)
        raise ValueError("boom")

    runner = WorkflowRunner()
    with patch("run.main", side_effect=fake_main):
        result = runner.run_capturing(RunSpec())

    assert result.status is RunStatus.FAILED
    assert len(result.artifact_references) == 1
    assert result.artifact_references[0].artifact_type == "story"


def test_run_still_propagates_exception_with_populated_collector(tmp_path):
    def fake_main(**kwargs):
        collector = kwargs["artifact_collector"]
        collector.bind(tmp_path)
        _write_story(tmp_path)
        collector.register_stage_outputs(STAGE_INTAKE)
        raise ValueError("boom")

    runner = WorkflowRunner()
    with patch("run.main", side_effect=fake_main):
        with pytest.raises(ValueError, match="boom"):
            runner.run(RunSpec())


# --------------------------------------------------------------------------- #
# 14-15. Sink absence / sink failure are nonfatal                            #
# --------------------------------------------------------------------------- #


def test_none_sink_still_collects_references(tmp_path):
    collector = LiveArtifactCollector(run_id="run-1", sink=None)
    collector.bind(tmp_path)
    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)
    assert len(collector.references) == 1
    assert collector.references[0].validation_status is ArtifactValidationStatus.VALID


def test_raising_sink_does_not_prevent_reference_collection(tmp_path):
    sink = _RaisingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_story(tmp_path)
    collector.register_stage_outputs(STAGE_INTAKE)  # must not raise
    assert len(collector.references) == 1
    assert sink.closed is False


# --------------------------------------------------------------------------- #
# 16. Existing `run.main` signature stays backward compatible                #
# --------------------------------------------------------------------------- #


def test_run_main_artifact_collector_param_defaults_to_none():
    import inspect

    sig = inspect.signature(run.main)
    assert "artifact_collector" in sig.parameters
    assert sig.parameters["artifact_collector"].default is None


# --------------------------------------------------------------------------- #
# 19-20. A1 — a missing per-UoW sibling never taints a present one           #
# --------------------------------------------------------------------------- #


def test_a1_missing_uow_spec_sibling_does_not_taint_present_one(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_assignment(tmp_path, ("U1", "U2"))
    _write_uow_spec(tmp_path, "U1")  # U2's uow_spec.yaml is never written
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)

    uow_refs = [r for r in collector.references if r.artifact_type == "uow_spec"]
    assert len(uow_refs) == 1
    assert uow_refs[0].validation_status is ArtifactValidationStatus.VALID
    assert "U1" in str(uow_refs[0].path)

    uow_events = [e for e in sink.events if e.metadata.get("artifact_type") == "uow_spec"]
    types = [e.event_type.value for e in uow_events]
    assert "artifact.validated" in types
    assert "artifact.missing" in types
    assert "artifact.invalid" not in types


def test_a1_missing_impl_report_sibling_does_not_taint_present_one(tmp_path):
    sink = _RecordingSink()
    collector = LiveArtifactCollector(run_id="run-1", sink=sink)
    collector.bind(tmp_path)
    _write_assignment(tmp_path, ("U1", "U2"))
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)  # caches uow_ids
    sink.events.clear()

    _write_impl_report(tmp_path, "U1")  # U2's impl_report.yaml is never written
    collector.register_stage_outputs(STAGE_EXECUTION)

    impl_refs = [r for r in collector.references if r.artifact_type == "impl_report"]
    assert len(impl_refs) == 1
    assert impl_refs[0].validation_status is ArtifactValidationStatus.VALID

    impl_events = [e for e in sink.events if e.metadata.get("artifact_type") == "impl_report"]
    types = [e.event_type.value for e in impl_events]
    assert "artifact.validated" in types
    assert "artifact.missing" in types


# --------------------------------------------------------------------------- #
# 21. A2 — typed-invalid assignment never blinds UoW discovery               #
# --------------------------------------------------------------------------- #


def test_a2_typed_invalid_assignment_does_not_blind_uow_discovery(tmp_path):
    collector = LiveArtifactCollector(run_id="run-1", sink=None)
    collector.bind(tmp_path)
    # Legacy-readable (uow_id present) but typed-invalid: `source_task_id` is
    # required by `AssignmentArtifact._parse_uow` but not by the permissive
    # legacy `load_assignments_file` path.
    _write_json(tmp_path / "planning/assignments.json", {
        "batches": [
            {"batch_id": 1, "uows": [{"uow_id": "U1"}, {"uow_id": "U2"}]},
        ],
    })
    _write_uow_spec(tmp_path, "U1")
    _write_uow_spec(tmp_path, "U2")
    collector.register_stage_outputs(STAGE_TASK_ASSIGNMENT)

    assignment_ref = next(r for r in collector.references if r.artifact_type == "assignment")
    assert assignment_ref.validation_status is ArtifactValidationStatus.INVALID

    uow_refs = [r for r in collector.references if r.artifact_type == "uow_spec"]
    assert len(uow_refs) == 2
    assert all(r.validation_status is ArtifactValidationStatus.VALID for r in uow_refs)


# --------------------------------------------------------------------------- #
# 22. A3 — real `run.main` wiring, exact hook sequence                       #
# --------------------------------------------------------------------------- #


class _SequenceRecordingCollector:
    """Records (method, stage) call order — proves real `run.main` wiring
    without depending on on-disk fixtures (`_require_file`/`load_assignments`
    are mocked in this harness, matching `test_run_worktree_integration.py`).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def bind(self, change_root) -> None:
        self.calls.append(("bind", str(change_root)))

    def emit_stage_inputs(self, stage: str) -> None:
        self.calls.append(("emit_stage_inputs", stage))

    def register_stage_outputs(self, stage: str) -> None:
        self.calls.append(("register_stage_outputs", stage))


def _run_main_with_collector(collector, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_RUNNER_EVALUATION_RUN", raising=False)
    wi = _workflow_input()
    with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
        _install_fake_workflow_modules(stack)
        _base_patches(stack, tmpdir, wi)
        stack.enter_context(patch.object(run, "change_run_lock", return_value=_make_mock_lock(True)))
        stack.enter_context(patch.object(run, "working_tree_lock", return_value=_make_mock_lock(True)))
        stack.enter_context(patch.object(run, "git_admin_lock", return_value=_make_mock_lock(True)))
        stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/x"))
        stack.enter_context(patch.object(run, "ensure_graphify_index"))
        return run.main(
            repo="/tmp/repo",
            story_file="/tmp/story.json",
            skip_materialize=True,
            artifact_collector=collector,
        )


def test_a3_real_run_main_wiring_calls_hooks_in_exact_order(monkeypatch):
    collector = _SequenceRecordingCollector()
    _run_main_with_collector(collector, monkeypatch)

    assert collector.calls[0][0] == "bind"
    assert collector.calls[1:] == [
        ("register_stage_outputs", STAGE_INTAKE),
        ("emit_stage_inputs", STAGE_TASK_GENERATION),
        ("register_stage_outputs", STAGE_TASK_GENERATION),
        ("emit_stage_inputs", STAGE_TASK_ASSIGNMENT),
        ("register_stage_outputs", STAGE_TASK_ASSIGNMENT),
        ("emit_stage_inputs", STAGE_EXECUTION),
        ("register_stage_outputs", STAGE_EXECUTION),
        ("emit_stage_inputs", STAGE_QA),
        ("register_stage_outputs", STAGE_QA),
        ("emit_stage_inputs", STAGE_PR_REVIEW),
    ]


# --------------------------------------------------------------------------- #
# 23. Failure precedence — a poisoned collector never changes the outcome    #
# --------------------------------------------------------------------------- #


def test_failure_precedence_poisoned_collector_never_changes_workflow_result(monkeypatch):
    """Even when the collector's internal logic raises on every call, the
    real `LiveArtifactCollector`'s no-throw public boundary must swallow it —
    `run.main`'s own return value is identical with or without the collector.
    """
    poisoned = LiveArtifactCollector(run_id="run-x", sink=None)
    poisoned._emit_stage_inputs = Mock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    poisoned._register_stage_outputs = Mock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    baseline = _run_main_with_collector(None, monkeypatch)
    poisoned_result = _run_main_with_collector(poisoned, monkeypatch)

    assert poisoned_result == baseline
    # The poisoned internals really were exercised -- this proves the public
    # boundary's guard did the work, not that the hooks were simply unreached.
    assert poisoned._emit_stage_inputs.called
    assert poisoned._register_stage_outputs.called
