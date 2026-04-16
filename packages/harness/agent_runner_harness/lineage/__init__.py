"""Lineage construction and persistence.

Builds RunLineage records from run inputs and writes them to disk.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_runner_shared.models import AgentRef, RunLineage, WorkflowRef
from agent_runner_shared.util import iso_now


def build_lineage(
    *,
    run_id: str,
    cycle_id: str | None,
    runner_version: str,
    workflow_id: str,
    workflow_version: int,
    agent_refs: list[str],
    task_id: str,
    task_version: int,
    substrate_commit: str,
    substrate_ref: str,
    models: dict[str, str],
    judge_model: str,
    cassette_mode: str = "live",
    cassette_id: str | None = None,
    seed: int | None = None,
    mode: str = "dev",
    container_image_digest: str | None = None,
    k: int = 1,
    k_index: int = 0,
) -> RunLineage:
    """Construct a RunLineage from run inputs.

    Args:
        run_id: Unique identifier for this run.
        cycle_id: Identifier for the enclosing cycle, if any.
        runner_version: Version string of the runner.
        workflow_id: Workflow identifier.
        workflow_version: Workflow version integer.
        agent_refs: List of 'name@version' agent reference strings.
        task_id: Task identifier.
        task_version: Task version integer.
        substrate_commit: Git commit SHA of the substrate.
        substrate_ref: Human-readable substrate ref label.
        models: Dict mapping role -> model identifier.
        judge_model: Model used for rubric judging.
        cassette_mode: One of 'live', 'record', 'replay'.
        cassette_id: Cassette identifier, if applicable.
        seed: Random seed, if applicable.
        mode: 'authoritative' or 'dev'.
        container_image_digest: Docker image digest, if run in container.
        k: Total number of parallel runs.
        k_index: Index of this run within the k-run set.

    Returns:
        Constructed RunLineage model.
    """
    return RunLineage(
        run_id=run_id,
        cycle_id=cycle_id,
        started_at=iso_now(),
        runner_version=runner_version,
        workflow=WorkflowRef(id=workflow_id, version=workflow_version),
        agent_versions=[AgentRef.parse(r) for r in agent_refs],
        task_id=task_id,
        task_version=task_version,
        substrate_commit=substrate_commit,
        substrate_ref=substrate_ref,
        models=models,
        judge_model=judge_model,
        cassette_mode=cassette_mode,  # type: ignore[arg-type]
        cassette_id=cassette_id,
        seed=seed,
        mode=mode,  # type: ignore[arg-type]
        container_image_digest=container_image_digest,
        k=k,
        k_index=k_index,
    )


def persist(lineage: RunLineage, run_dir: Path) -> Path:
    """Persist a RunLineage to lineage.json in run_dir.

    Args:
        lineage: The RunLineage to persist.
        run_dir: Directory for this run's outputs.

    Returns:
        Path to the written lineage.json file.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / "lineage.json"
    dest.write_text(lineage.model_dump_json(indent=2), encoding="utf-8")
    return dest
