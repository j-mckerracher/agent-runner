"""Benchmark report aggregation for the Evaluate view."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eval.runner import DEFAULT_BENCHMARKS, DEFAULT_REPORTS, LEGACY_BENCHMARKS, LEGACY_REPORTS

logger = logging.getLogger(__name__)


def _score_to_percent(value: Any) -> int | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0:
        return None
    if score <= 1:
        score *= 100.0
    return round(min(score, 100.0))


def list_eval_reports(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or DEFAULT_REPORTS
    if root == DEFAULT_REPORTS and (not root.is_dir() or not any(root.glob("*.json"))) and LEGACY_REPORTS.is_dir():
        root = LEGACY_REPORTS
    reports: list[dict[str, Any]] = []
    if not root.is_dir():
        return reports
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name == "latest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        reports.append(
            {
                "path": str(path),
                "name": path.name,
                "created_at": payload.get("created_at"),
                "repo": payload.get("repo"),
                "sha": payload.get("sha"),
                "runner": payload.get("runner"),
                "model": payload.get("model"),
                "runs": payload.get("runs"),
                "summary": payload.get("summary") or {},
                "results": payload.get("results", []),
            }
        )
    return reports


def list_benchmark_stories(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or DEFAULT_BENCHMARKS
    if root == DEFAULT_BENCHMARKS and not any((root / difficulty / "story.json").is_file() for difficulty in ("easy", "medium", "hard")):
        if any((LEGACY_BENCHMARKS / difficulty / "story.json").is_file() for difficulty in ("easy", "medium", "hard")):
            root = LEGACY_BENCHMARKS
    stories: list[dict[str, Any]] = []
    for difficulty in ("easy", "medium", "hard"):
        story_path = root / difficulty / "story.json"
        if not story_path.is_file():
            continue
        try:
            data = json.loads(story_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stories.append({
            "difficulty": difficulty,
            "change_id": data.get("change_id", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "acceptance_criteria": data.get("acceptance_criteria", []),
        })
    return stories


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _result_warnings(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    hidden = result.get("hidden_tests") or {}
    if int(hidden.get("skipped") or 0):
        warnings.append("Hidden tests skipped.")
    ac_results = hidden.get("ac_results") or {}
    if any(ac.get("missing_cases") for ac in ac_results.values()):
        warnings.append("Benchmark report/AC mapping is incomplete.")
    story_acs = (result.get("story") or {}).get("acceptance_criteria") or []
    if story_acs and len(ac_results) < len(story_acs):
        warnings.append("Benchmark report/AC mapping is incomplete.")
    return warnings


def _group_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in report.get("results") or []:
        grouped.setdefault(str(result.get("name") or "unknown"), []).append(result)

    rows: list[dict[str, Any]] = []
    report_warnings = (report.get("summary") or {}).get("warnings") or []
    _DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}
    for name, results in sorted(grouped.items(), key=lambda item: _DIFFICULTY_ORDER.get(item[0], 99)):
        scores = [float((result.get("quality") or {}).get("weighted_score") or 0.0) for result in results]
        wall_times = [float((result.get("metrics") or {}).get("wall_seconds") or result.get("seconds") or 0.0) for result in results]
        tokens = [float((result.get("metrics") or {}).get("tokens_total") or 0.0) for result in results]
        passes = sum(1 for result in results if result.get("status") == "PASS")
        story = results[0].get("story") or {}
        warnings = list(dict.fromkeys([*report_warnings, *[warning for result in results for warning in _result_warnings(result)]]))
        rows.append(
            {
                "task": name,
                "title": story.get("title") or "Benchmark report",
                "current": _mean(scores),
                "baseline": None,
                "delta": None,
                "runs": len(results),
                "status": "pass" if passes == len(results) else "failed",
                "score_source": "benchmark_report",
                "pass_rate": round(passes / len(results), 4) if results else 0.0,
                "wall_seconds_mean": _mean(wall_times),
                "tokens_total_mean": _mean(tokens),
                "warnings": warnings,
                "story": story,
                "details": results,
                "report": {
                    "name": report.get("name"),
                    "path": report.get("path"),
                    "created_at": report.get("created_at"),
                    "runner": report.get("runner"),
                    "model": report.get("model"),
                    "sha": report.get("sha"),
                },
            }
        )
    return rows


def summary() -> dict[str, Any]:
    reports = list_eval_reports()
    if not reports:
        return {
            "source": "benchmark_reports",
            "overall_pass_rate": None,
            "regressions": 0,
            "total_runs": 0,
            "avg_cost_usd": None,
            "rows": [],
            "reports": [],
            "verdict": "insufficient data",
            "quality_score": None,
            "pass_rate": None,
            "wall_seconds_mean": None,
            "tokens_total_mean": None,
            "warnings": ["No benchmark reports found.", "No baseline selected."],
        }

    latest = reports[0]
    latest_summary = latest.get("summary") or {}
    quality = latest_summary.get("quality") or {}
    reliability = latest_summary.get("reliability") or {}
    efficiency = latest_summary.get("efficiency") or {}
    rows = _group_rows(latest)
    warnings = list(latest_summary.get("warnings") or [])
    if not warnings:
        warnings = []
    if any(row.get("score_source") != "benchmark_report" for row in rows):
        warnings.append("Results are based on legacy job status instead of benchmark report scoring.")
    warnings = list(dict.fromkeys(warnings))
    return {
        "source": "benchmark_reports",
        "overall_pass_rate": _score_to_percent(reliability.get("pass_rate")),
        "regressions": 1 if latest_summary.get("trend") == "decreased" else 0,
        "total_runs": reliability.get("runs") or 0,
        "avg_cost_usd": efficiency.get("cost_usd_mean"),
        "rows": rows,
        "reports": reports,
        "verdict": latest_summary.get("trend") or "insufficient data",
        "quality_score": quality.get("weighted_score"),
        "pass_rate": reliability.get("pass_rate"),
        "wall_seconds_mean": efficiency.get("wall_seconds_mean"),
        "tokens_total_mean": efficiency.get("tokens_total_mean"),
        "warnings": warnings,
    }
