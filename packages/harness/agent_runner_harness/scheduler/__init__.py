"""Scheduler — top-level run coordinator.

Orchestrates materialize → substrate → launch runner → grade → lineage
for one or more tasks in a single evaluation cycle.
"""
from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runner_shared.models import Task
from agent_runner_shared.util import iso_now


@dataclass
class RunOpts:
    """Options for a run cycle."""

    k_runs: int = 1
    parallel: int = 1
    cassette_mode: str = "live"
    dev_mode: bool = True
    judge_model: str = "gpt-5.4-high"
    judge_stub: bool = False
    sources_dir: Path = Path("agent-sources")
    runs_root: Path = Path("runs")
    substrates_path: Path = Path("substrates/substrates.yaml")
    image: str | None = None
    corpus_dir: Path = Path("task-corpus")
    baselines_dir: Path = Path("baselines")


@dataclass
class CycleResult:
    """Result of a complete evaluation cycle."""

    cycle_id: str
    started_at: str
    completed_at: str
    status: str
    run_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _run_single(
    task: Task,
    cycle_id: str,
    k_index: int,
    opts: RunOpts,
    run_id: str,
) -> dict[str, Any]:
    """Execute a single run for a task (dev mode: subprocess)."""
    from agent_runner_shared.events import emit_event_line
    from agent_runner_harness.grading import grade
    from agent_runner_harness.lineage import build_lineage, persist
    from agent_runner_harness.substrates import load_manifest, resolve as resolve_substrate, extract_substrate

    run_dir = opts.runs_root / cycle_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    event_log = run_dir / "event.log.jsonl"

    print(emit_event_line("run.start", run_id=run_id, task_id=task.id, k_index=k_index))

    # Resolve and extract substrate into a per-run workspace
    substrate_commit = "HEAD"
    substrate_ref = task.substrate.ref
    workspace_dir = run_dir / "workspace"

    try:
        manifest = load_manifest(opts.substrates_path)
        entry = resolve_substrate(substrate_ref, manifest)
        substrate_commit = extract_substrate(entry, workspace_dir)
    except Exception:
        workspace_dir.mkdir(parents=True, exist_ok=True)

    # In dev mode: skip container, run runner-as-subprocess if available
    returncode = 0
    runner_error: str | None = None

    if opts.dev_mode:
        # Try to invoke the runner CLI if available
        try:
            task_file = opts.corpus_dir / task.id / "task.yaml"
            if not task_file.exists():
                task_file = opts.corpus_dir / task.id / "task.json"

            runner_args = [
                "python3", "-m", "agent_runner.cli.headless",
                "--workflow", task.workflow.id,
                "--task-spec", str(task_file),
                "--working-copy", str(workspace_dir),
                "--agents-dir", str(artifact_dir / ".claude" / "agents"),
                "--artifact-dir", str(artifact_dir),
                "--event-log", str(event_log),
                "--json-log", str(run_dir / "run.log.json"),
                "--dry-run",
            ]
            if task.seed is not None:
                runner_args += ["--seed", str(task.seed)]

            result = subprocess.run(
                runner_args,
                capture_output=True,
                text=True,
                timeout=120,
            )
            returncode = result.returncode
            if result.stdout:
                with event_log.open("a") as f:
                    for line in result.stdout.splitlines():
                        if line.strip():
                            f.write(line + "\n")
        except Exception as exc:
            runner_error = str(exc)
            returncode = 3
    else:
        # Authoritative mode: run in container, bind-mounting the workspace
        from agent_runner_harness.container import run_in_container

        task_file = opts.corpus_dir / task.id / "task.yaml"
        if not task_file.exists():
            task_file = opts.corpus_dir / task.id / "task.json"

        container_command = [
            "python3", "-m", "agent_runner.cli.headless",
            "--workflow", task.workflow.id,
            "--task-spec", str(task_file),
            "--working-copy", "/workspace/repo",
            "--agents-dir", "/workspace/agents",
            "--artifact-dir", "/workspace/artifacts",
            "--event-log", "/workspace/event.log.jsonl",
            "--json-log", "/workspace/run.log.json",
            "--dry-run",
        ]
        if task.seed is not None:
            container_command += ["--seed", str(task.seed)]

        container_result = run_in_container(
            image=opts.image or "",
            working_copy=workspace_dir,
            env={},
            command=container_command,
        )
        returncode = container_result.returncode

    # Build lineage
    lineage = build_lineage(
        run_id=run_id,
        cycle_id=cycle_id,
        runner_version="0.1.0",
        workflow_id=task.workflow.id,
        workflow_version=task.workflow.version,
        agent_refs=task.agents,
        task_id=task.id,
        task_version=task.version,
        substrate_commit=substrate_commit,
        substrate_ref=substrate_ref,
        models=task.models,
        judge_model=opts.judge_model,
        cassette_mode=opts.cassette_mode,
        seed=task.seed,
        mode="dev" if opts.dev_mode else "authoritative",
        k=opts.k_runs,
        k_index=k_index,
    )
    persist(lineage, run_dir)

    # Grade
    task_dir = opts.corpus_dir / task.id
    grading_record = grade(
        task,
        artifact_dir,
        event_log,
        opts.judge_model,
        stub=opts.judge_stub,
        task_dir=task_dir,
        run_id=run_id,
    )

    # Write grading.json
    (run_dir / "grading.json").write_text(
        grading_record.model_dump_json(indent=2), encoding="utf-8"
    )

    print(emit_event_line(
        "run.end",
        run_id=run_id,
        task_id=task.id,
        overall_pass=grading_record.overall_pass,
        returncode=returncode,
    ))

    return {
        "run_id": run_id,
        "task_id": task.id,
        "overall_pass": grading_record.overall_pass,
        "reason": grading_record.reason,
        "returncode": returncode,
        "error": runner_error,
        "run_dir": str(run_dir),
    }


def run_cycle(
    tasks: list[Task],
    opts: RunOpts,
) -> CycleResult:
    """Run a full evaluation cycle over a list of tasks.

    For each task, runs k_runs times (sequentially in this implementation).
    Orchestrates materialization, substrate resolution, runner launch, and grading.

    Args:
        tasks: List of tasks to evaluate.
        opts: RunOpts with configuration.

    Returns:
        CycleResult with per-run results.
    """
    cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
    started_at = iso_now()
    run_results: list[dict[str, Any]] = []
    errors: list[str] = []

    for task in tasks:
        for k_index in range(opts.k_runs):
            run_id = f"run-{uuid.uuid4().hex[:8]}"
            try:
                result = _run_single(task, cycle_id, k_index, opts, run_id)
                run_results.append(result)
            except Exception as exc:
                err = f"Run {run_id} for task {task.id} failed: {exc}"
                errors.append(err)
                run_results.append({
                    "run_id": run_id,
                    "task_id": task.id,
                    "overall_pass": False,
                    "reason": str(exc),
                    "error": str(exc),
                })

    completed_at = iso_now()
    status = "completed" if not errors else "completed_with_errors"

    return CycleResult(
        cycle_id=cycle_id,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        run_results=run_results,
        errors=errors,
    )
