"""Cycle report renderer.

Renders a human-readable markdown report for a completed evaluation cycle
and optionally writes it to runs/<cycle_id>/report.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def render_cycle_report(cycle_result: Any) -> str:
    """Render a markdown report for a cycle result.

    Args:
        cycle_result: CycleResult object from the scheduler.

    Returns:
        Markdown string.
    """
    lines: list[str] = []
    lines.append(f"# Cycle Report: {cycle_result.cycle_id}")
    lines.append("")
    lines.append(f"**Started:** {cycle_result.started_at}")
    lines.append(f"**Completed:** {cycle_result.completed_at}")
    lines.append(f"**Status:** {cycle_result.status}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Tasks evaluated: {len(cycle_result.run_results)}")
    passed = sum(1 for r in cycle_result.run_results if r.get("overall_pass"))
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {len(cycle_result.run_results) - passed}")
    lines.append("")

    if cycle_result.run_results:
        lines.append("## Run Results")
        lines.append("")
        lines.append("| Run ID | Task | Pass | Reason |")
        lines.append("|--------|------|------|--------|")
        for r in cycle_result.run_results:
            run_id = r.get("run_id", "?")
            task_id = r.get("task_id", "?")
            overall = "✅" if r.get("overall_pass") else "❌"
            reason = r.get("reason", "")[:60]
            lines.append(f"| {run_id} | {task_id} | {overall} | {reason} |")
        lines.append("")

    if cycle_result.errors:
        lines.append("## Errors")
        lines.append("")
        for err in cycle_result.errors:
            lines.append(f"- {err}")
        lines.append("")

    return "\n".join(lines)


def write_report(cycle_result: Any, runs_root: Path) -> Path:
    """Write a cycle report to runs/<cycle_id>/report.md.

    Args:
        cycle_result: CycleResult object.
        runs_root: Root directory for run outputs.

    Returns:
        Path to the written report file.
    """
    runs_root = Path(runs_root)
    cycle_dir = runs_root / cycle_result.cycle_id
    cycle_dir.mkdir(parents=True, exist_ok=True)
    report_path = cycle_dir / "report.md"
    report_path.write_text(render_cycle_report(cycle_result), encoding="utf-8")
    return report_path
