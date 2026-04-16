"""Grading package entrypoint.

Provides the top-level grade() function and re-exports grading utilities.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from agent_runner_shared.models import AcceptanceCriterion, GradingRecord, Task
from agent_runner_shared.util import iso_now

from .deterministic import grade_deterministic
from .rubric import grade_rubric

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict[str, Any]:
    """Load grading configuration."""
    if _CONFIG_PATH.exists():
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def grade(
    task: Task,
    run_artifact_dir: Path,
    event_log_path: Path,
    judge_model: str,
    *,
    stub: bool = False,
    task_dir: Path | None = None,
    judge_fn: Callable[[str, str], dict[str, Any]] | None = None,
    run_id: str = "",
) -> GradingRecord:
    """Grade a completed run against a task's acceptance criteria.

    Args:
        task: The Task definition with acceptance criteria.
        run_artifact_dir: Directory containing run artifacts.
        event_log_path: Path to the JSONL event log.
        judge_model: Model identifier used for rubric judging.
        stub: If True, use stub judge (no LLM calls).
        task_dir: Path to the task definition directory (for script paths).
        judge_fn: Injectable judge function for rubric grading.
        run_id: Run identifier for the GradingRecord.

    Returns:
        GradingRecord with all criterion results and overall_pass.
    """
    det_criteria = [
        AcceptanceCriterion.model_validate(c.model_dump())
        for c in task.acceptance_criteria.get("deterministic", [])
    ]
    rub_criteria = [
        AcceptanceCriterion.model_validate(c.model_dump())
        for c in task.acceptance_criteria.get("rubric", [])
    ]

    det_results = grade_deterministic(
        det_criteria,
        run_artifact_dir,
        event_log_path,
        task_dir=task_dir,
    )

    rub_results = grade_rubric(
        rub_criteria,
        run_artifact_dir,
        judge_fn=judge_fn,
        stub=stub,
    )

    all_results = det_results + rub_results
    overall_pass = all(r["passed"] for r in all_results) if all_results else False

    failed = [r["id"] for r in all_results if not r["passed"]]
    reason = f"Failed criteria: {', '.join(failed)}" if failed else "All criteria passed"

    return GradingRecord(
        run_id=run_id,
        task_id=task.id,
        task_version=task.version,
        deterministic=det_results,
        rubric=rub_results,
        overall_pass=overall_pass,
        reason=reason,
        judge_model=judge_model,
        graded_at=iso_now(),
    )


__all__ = ["grade", "grade_deterministic", "grade_rubric"]
