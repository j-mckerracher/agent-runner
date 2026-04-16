"""Cross-package end-to-end tests.

These tests verify the top-level architecture contracts by importing from
all four packages and exercising a minimal end-to-end flow:

  1. Load the ``standard`` workflow definition.
  2. Load all agent bundles from ``agent-sources/``.
  3. Resolve all workflow agent refs against the registry.
  4. Materialize resolved bundles into a temp directory.
  5. Assert every expected ``.agent.md`` file is present and non-empty.
  6. Load all 3 seed corpus tasks via the harness corpus loader.
  7. Build a ``RunLineage`` object and verify JSON roundtrip.
  8. Verify backward-compat CLI scripts still launch (exit code 0).

The tests are designed to run from the repo root with:
  PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness pytest -q
or with just ``pytest -q`` when conftest.py adds the packages to sys.path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Repo root is two levels up from this file: tests/test_end_to_end.py → tests/ → repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "task-corpus"
SOURCES_DIR = REPO_ROOT / "agent-sources"

# Expected seed task IDs
SEED_TASK_IDS = {
    "ado-normalize-embedded-ac",
    "simple-readme-update",
    "refactor-util-module",
}


class TestWorkflowLoading:
    def test_load_standard_workflow(self) -> None:
        """Standard workflow loads and exposes expected fields."""
        from agent_runner.workflow.definition import load_workflow

        wf = load_workflow("standard")
        assert wf.id == "standard"
        assert wf.version >= 1
        assert len(wf.stages) > 0
        assert wf.description

    def test_workflow_agent_refs_nonempty(self) -> None:
        """Standard workflow references at least one agent."""
        from agent_runner.workflow.definition import load_workflow

        wf = load_workflow("standard")
        refs = wf.agent_refs()
        assert len(refs) > 0
        for ref in refs:
            assert "@" in ref, f"Agent ref {ref!r} is not in name@version format"

    def test_workflow_stages_have_required_fields(self) -> None:
        """Every stage has id, kind, and the correct agent/producer/evaluator fields."""
        from agent_runner.workflow.definition import load_workflow

        wf = load_workflow("standard")
        for stage in wf.stages:
            assert stage.id
            assert stage.kind in ("single", "producer_evaluator_loop")
            if stage.kind == "single":
                assert stage.agent, f"Stage {stage.id!r} (single) missing agent"
            else:
                assert stage.producer, f"Stage {stage.id!r} (loop) missing producer"
                assert stage.evaluator, f"Stage {stage.id!r} (loop) missing evaluator"


class TestRegistryMaterialization:
    def test_load_bundles(self) -> None:
        """load_bundles discovers all agent bundles in agent-sources/."""
        from agent_runner_registry import load_bundles

        bundles = load_bundles(SOURCES_DIR)
        assert len(bundles) > 0, "No bundles found in agent-sources/"

    def test_resolve_workflow_refs(self) -> None:
        """All agent refs from the standard workflow can be resolved."""
        from agent_runner.workflow.definition import load_workflow
        from agent_runner_registry import load_bundles, resolve

        wf = load_workflow("standard")
        bundles = load_bundles(SOURCES_DIR)
        refs = wf.agent_refs()

        # This should not raise LookupError
        resolved = resolve(refs, bundles)
        assert len(resolved) == len(refs)

    def test_materialize_into_temp_dir(self, tmp_path: Path) -> None:
        """Materializing workflow agents writes .agent.md files."""
        from agent_runner.workflow.definition import load_workflow
        from agent_runner_registry import load_bundles, resolve, materialize

        wf = load_workflow("standard")
        bundles = load_bundles(SOURCES_DIR)
        resolved = resolve(wf.agent_refs(), bundles)

        target = tmp_path / ".claude" / "agents"
        manifest = materialize(resolved, target)

        assert target.is_dir()
        assert len(manifest.agents) == len(resolved)

        # Every materialized agent has a non-empty .agent.md (or named file)
        agent_files = list(target.glob("*.agent.md"))
        assert len(agent_files) == len(resolved), (
            f"Expected {len(resolved)} .agent.md files, found {len(agent_files)}"
        )
        for f in agent_files:
            assert f.stat().st_size > 0, f"{f.name} is empty after materialization"

    def test_materialization_manifest_written(self, tmp_path: Path) -> None:
        """materialize writes a .materialization.json manifest file."""
        from agent_runner.workflow.definition import load_workflow
        from agent_runner_registry import load_bundles, resolve, materialize
        import json

        wf = load_workflow("standard")
        bundles = load_bundles(SOURCES_DIR)
        resolved = resolve(wf.agent_refs(), bundles)
        target = tmp_path / ".claude" / "agents"

        materialize(resolved, target)

        manifest_file = target / ".materialization.json"
        assert manifest_file.exists()
        data = json.loads(manifest_file.read_text())
        assert "agents" in data
        assert "content_hashes" in data
        assert "materialized_at" in data


class TestCorpusLoading:
    def test_load_all_seed_tasks(self) -> None:
        """All 3 seed corpus tasks load and validate successfully."""
        from agent_runner_harness.corpus import load_all
        from agent_runner_shared.models import Task

        tasks = load_all(CORPUS_DIR)
        task_ids = {t.id for t in tasks}

        assert SEED_TASK_IDS.issubset(task_ids), (
            f"Missing tasks: {SEED_TASK_IDS - task_ids}"
        )
        for task in tasks:
            assert isinstance(task, Task)
            assert task.id
            assert task.version >= 1
            assert task.substrate.ref

    def test_task_acceptance_criteria_present(self) -> None:
        """Every seed task has at least one acceptance criterion."""
        from agent_runner_harness.corpus import load_all

        tasks = load_all(CORPUS_DIR)
        for task in tasks:
            total_criteria = (
                len(task.acceptance_criteria.get("deterministic") or [])
                + len(task.acceptance_criteria.get("rubric") or [])
            )
            assert total_criteria > 0, (
                f"Task {task.id!r} has no acceptance criteria"
            )

    def test_task_workflow_refs_valid(self) -> None:
        """Each seed task's workflow ref is loadable."""
        from agent_runner.workflow.definition import load_workflow
        from agent_runner_harness.corpus import load_all

        tasks = load_all(CORPUS_DIR)
        for task in tasks:
            wf = load_workflow(task.workflow.id)
            assert wf.id == task.workflow.id


class TestRunLineageRoundtrip:
    def test_build_and_roundtrip_run_lineage(self) -> None:
        """RunLineage built from resolved agent versions survives JSON roundtrip."""
        from agent_runner.workflow.definition import load_workflow
        from agent_runner_registry import load_bundles, resolve
        from agent_runner_shared.models import AgentRef, RunLineage, WorkflowRef

        wf = load_workflow("standard")
        bundles = load_bundles(SOURCES_DIR)
        resolved = resolve(wf.agent_refs(), bundles)
        agent_refs = [b.ref for b in resolved]

        lineage = RunLineage(
            run_id="run-e2e-test-001",
            cycle_id="cycle-e2e",
            runner_version="0.1.0",
            workflow=WorkflowRef(id=wf.id, version=wf.version),
            agent_versions=agent_refs,
            task_id="simple-readme-update",
            task_version=1,
            substrate_commit="abc123deadbeef",
            substrate_ref="baseline-2026-04-16",
            models={"worker": "claude-sonnet-4-5", "judge": "gpt-5.4-high"},
            judge_model="gpt-5.4-high",
            cassette_mode="live",
            mode="dev",
            k=5,
            k_index=0,
        )

        # Roundtrip via model_dump_json
        json_str = lineage.model_dump_json()
        restored = RunLineage.model_validate_json(json_str)

        assert restored.run_id == lineage.run_id
        assert restored.workflow.id == wf.id
        assert len(restored.agent_versions) == len(agent_refs)
        assert restored.mode == "dev"
        assert restored.k == 5

    def test_run_lineage_agent_versions_from_bundles(self) -> None:
        """AgentRef objects from bundles are correctly serialized in RunLineage."""
        from agent_runner_registry import load_bundles
        from agent_runner_shared.models import AgentRef, RunLineage, WorkflowRef

        bundles = load_bundles(SOURCES_DIR)
        # Pick a few bundles for the test
        sample_bundles = list(bundles.values())[:3]
        agent_refs = [b.ref for b in sample_bundles]

        lineage = RunLineage(
            run_id="run-agent-ref-test",
            runner_version="0.1.0",
            workflow=WorkflowRef(id="standard", version=1),
            agent_versions=agent_refs,
            task_id="simple-readme-update",
            task_version=1,
            substrate_commit="HEAD",
            substrate_ref="baseline-2026-04-16",
            models={},
            judge_model="gpt-5.4-high",
            cassette_mode="live",
        )

        json_str = lineage.model_dump_json()
        restored = RunLineage.model_validate_json(json_str)

        for orig, rest in zip(agent_refs, restored.agent_versions):
            assert str(orig) == str(rest), (
                f"AgentRef mismatch: {orig!r} vs {rest!r}"
            )


class TestBackwardCompatScripts:
    def test_run_headless_help(self) -> None:
        """run_headless.py --help exits 0 and prints usage."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "run_headless.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()

    def test_run_general_help(self) -> None:
        """run_general.py --help exits 0 and prints usage."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "run_general.py"), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()

    @pytest.mark.skip(
        reason=(
            "run.py is interactive-only: it exits with code 1 when stdin is not a "
            "terminal ('This runner is interactive only'). The non-interactive "
            "entrypoints run_headless.py and run_general.py are tested above."
        )
    )
    def test_run_py_exits_zero(self) -> None:
        """Skipped: run.py is interactive-only and exits 1 in a subprocess context."""
