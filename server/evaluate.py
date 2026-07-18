"""Benchmark report aggregation for the Evaluate view.

Thin v0.2 adapter: reads the canonical v0.2 `EvalReport` (see
`eval/report_schema.py`, `eval/live_report.py`) — the sole machine report as
of the Prompt 6 refactor — and maps it into the response shape the GUI
already expects (`gui/assets/js/content-views.js`). The GUI itself is not
touched; this module absorbs the shape difference.

Legacy (pre-v0.2) report files are no longer read here — this is an
intentional compatibility break for consumers of the old hand-rolled JSON
body (see the Prompt 6 plan). Metrics the v0.2 report marks
unknown/not_applicable/not_collected are surfaced as `None`, never coerced to
0 — the scorecard is read, not recomputed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from eval.runner import DEFAULT_BENCHMARKS, DEFAULT_REPORTS, LEGACY_BENCHMARKS

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


def _metric_value(metrics: dict[str, Any] | None, key: str) -> Any:
    """Extract a v0.2 `Metric.value`; `None` when unknown/not_collected/n_a.

    Never coerces a missing value to 0 — that's the whole point of the v0.2
    honest-missing-value policy (`eval/report_schema.py::MetricStatus`).
    """
    metric = (metrics or {}).get(key) or {}
    if metric.get("status") != "known":
        return None
    return metric.get("value")


def _ms_to_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / 1000.0, 4)
    except (TypeError, ValueError):
        return None


def _ac_pseudo_result(ac: dict[str, Any]) -> dict[str, Any]:
    """Map one v0.2 `AcceptanceCriteriaResult` to the legacy ac_results shape.

    `missing_cases` is the legacy signal for "no reliable evidence" — reused
    directly from AC status `unknown` (see `eval/live_report.py`'s
    positive-evidence-only aggregation).
    """
    status = ac.get("status")
    return {
        "passed": status == "pass",
        "cases": [],
        "missing_cases": status == "unknown",
    }


def _case_to_pseudo_results(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one v0.2 `BenchmarkCaseResult` into one legacy-shaped result
    dict per trial, so `_group_rows`' per-trial reliability display (runs,
    per-trial wall time, pass/fail) keeps working unchanged.

    AC/quality data is case-level in v0.2 (trials within a case share AC
    definitions by construction — see the case-fingerprint guard in
    `eval/live_report.py`), so it's duplicated across the case's trials here.
    """
    ac_results = case.get("acceptance_criteria_results") or []
    ac_total = len(ac_results)
    ac_passed = sum(1 for ac in ac_results if ac.get("status") == "pass")
    critical_ac_failed = sum(1 for ac in ac_results if ac.get("critical") and ac.get("status") == "fail")
    story = {
        "title": "",
        "acceptance_criteria": [f"{ac.get('ac_id', '')}: {ac.get('description', '')}" for ac in ac_results],
    }
    hidden_tests = {
        "skipped": 0,
        "ac_results": {ac.get("ac_id", ""): _ac_pseudo_result(ac) for ac in ac_results},
    }
    aggregate = case.get("aggregate_result") or {}
    weighted_score = aggregate.get("weighted_score_mean")

    trials = case.get("trial_results") or []
    results: list[dict[str, Any]] = []
    for trial in trials:
        artifacts = {ref.get("ref_id", ""): ref.get("uri", "") for ref in trial.get("artifact_refs") or []}
        results.append(
            {
                "name": case.get("benchmark_id", "unknown"),
                "status": "PASS" if trial.get("status") == "passed" else "FAIL",
                "error": trial.get("error_summary") or "",
                "trial_index": len(results) + 1,
                "quality": {
                    "weighted_score": weighted_score,
                    "ac_passed": ac_passed,
                    "ac_total": ac_total,
                    "critical_ac_failed": critical_ac_failed,
                    "hidden_tests_skipped": 0,
                },
                "metrics": {
                    "wall_seconds": _ms_to_seconds(_metric_value(trial, "duration_ms")),
                    "tokens_total": None,
                },
                "story": story,
                "hidden_tests": hidden_tests,
                "artifacts": artifacts,
            }
        )
    return results or [
        {
            "name": case.get("benchmark_id", "unknown"),
            "status": "FAIL",
            "error": "no trials recorded",
            "trial_index": 1,
            "quality": {"weighted_score": weighted_score, "ac_passed": ac_passed, "ac_total": ac_total, "critical_ac_failed": critical_ac_failed, "hidden_tests_skipped": 0},
            "metrics": {"wall_seconds": None, "tokens_total": None},
            "story": story,
            "hidden_tests": hidden_tests,
            "artifacts": {},
        }
    ]


def _adapt_report(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    """Map one v0.2 `EvalReport` payload into the legacy per-report shape
    `list_eval_reports`/`_group_rows`/`summary` (and the GUI) expect."""
    metadata = payload.get("metadata") or {}
    scorecard = payload.get("scorecard") or {}
    capability = scorecard.get("capability") or {}
    reliability = scorecard.get("reliability") or {}
    efficiency = scorecard.get("efficiency") or {}
    comparison_context = (payload.get("summary") or {}).get("comparison_context") or {}
    cases = payload.get("benchmark_case_results") or []

    results: list[dict[str, Any]] = []
    for case in cases:
        results.extend(_case_to_pseudo_results(case))

    summary = {
        "quality": {"weighted_score": _metric_value(capability, "ac_pass_rate")},
        "reliability": {
            "pass_rate": _metric_value(reliability, "trial_pass_rate"),
            "runs": sum(len(case.get("trial_results") or []) for case in cases),
        },
        "efficiency": {
            "wall_seconds_mean": _ms_to_seconds(_metric_value(efficiency, "wall_clock_duration_ms")),
            "tokens_total_mean": _metric_value(efficiency, "token_usage"),
            "cost_usd_mean": _metric_value(efficiency, "estimated_cost"),
        },
        "trend": comparison_context.get("trend") or "insufficient data",
        "warnings": list(comparison_context.get("warnings") or []),
    }

    return {
        "path": str(path),
        "name": path.name,
        "created_at": payload.get("created_at"),
        "repo": metadata.get("repo"),
        "sha": payload.get("candidate_version"),
        "runner": metadata.get("runner"),
        "model": metadata.get("model"),
        "runs": summary["reliability"]["runs"],
        "summary": summary,
        "results": results,
    }


def list_eval_reports(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or DEFAULT_REPORTS
    if not root.is_dir():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.name == "latest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "benchmark_case_results" not in payload:
            # Not a v0.2 report (e.g. a stale pre-refactor file). v0.2 is the
            # sole machine report going forward, so skip rather than guess.
            logger.debug("list_eval_reports: skipping non-v0.2 report %s", path)
            continue
        reports.append(_adapt_report(payload, path))
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


def _mean(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return round(sum(known) / len(known), 4) if known else None


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
        scores = [(result.get("quality") or {}).get("weighted_score") for result in results]
        wall_times = [(result.get("metrics") or {}).get("wall_seconds") for result in results]
        tokens = [(result.get("metrics") or {}).get("tokens_total") for result in results]
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
