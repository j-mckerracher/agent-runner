"""Baseline band computation, persistence, and loading.

Manages per-task pass-rate bands that track expected performance ranges.
Bands are stored as JSON files under baselines/<task_id>.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_runner_shared.models import BaselineBand
from agent_runner_shared.util import iso_now


def compute_band(
    pass_rates: list[bool | float],
    *,
    judge_model: str,
    task_id: str,
    task_version: int,
    reason: str = "initial",
) -> BaselineBand:
    """Compute a BaselineBand from a list of pass/fail or float pass rates.

    The band is mean ± 15 percentage points, clamped to [0, 1].
    When sample_size < 2, high == low == mean.

    Args:
        pass_rates: List of boolean pass/fail values or float pass rates [0,1].
        judge_model: Model used for rubric judging in this calibration.
        task_id: Task identifier.
        task_version: Task version integer.
        reason: Human-readable reason for this band computation.

    Returns:
        Computed BaselineBand.
    """
    floats = [float(r) for r in pass_rates]
    n = len(floats)
    if n == 0:
        mean = 0.0
    else:
        mean = sum(floats) / n

    if n < 2:
        low = mean
        high = mean
    else:
        low = max(0.0, mean - 0.15)
        high = min(1.0, mean + 0.15)

    return BaselineBand(
        task_id=task_id,
        task_version=task_version,
        low=low,
        high=high,
        mean=mean,
        sample_size=n,
        established_at=iso_now(),
        judge_model=judge_model,
        reason=reason,
    )


def load_band(baselines_dir: Path, task_id: str) -> BaselineBand | None:
    """Load a baseline band for a task, or None if not found.

    Args:
        baselines_dir: Root directory for baseline band files.
        task_id: Task identifier to look up.

    Returns:
        Loaded BaselineBand, or None if no band file exists.
    """
    baselines_dir = Path(baselines_dir)
    band_file = baselines_dir / f"{task_id}.json"
    if not band_file.exists():
        return None
    data = json.loads(band_file.read_text(encoding="utf-8"))
    return BaselineBand.model_validate(data)


def save_band(band: BaselineBand, baselines_dir: Path) -> Path:
    """Save a baseline band to disk.

    Args:
        band: The BaselineBand to save.
        baselines_dir: Root directory for baseline band files.

    Returns:
        Path to the written file.
    """
    baselines_dir = Path(baselines_dir)
    baselines_dir.mkdir(parents=True, exist_ok=True)
    dest = baselines_dir / f"{band.task_id}.json"
    dest.write_text(band.model_dump_json(indent=2), encoding="utf-8")
    return dest
