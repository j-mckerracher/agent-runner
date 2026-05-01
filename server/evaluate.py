"""Aggregate metrics for the Evaluate view."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db, corpus

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


def _extract_weighted_score(row: dict[str, Any]) -> int | None:
    """Return persisted weighted eval score when a future DB row exposes one.

    The current jobs table does not persist eval scoring metrics; it only stores
    job status/cost/token fields. These lookups are intentionally optional so
    /evaluate/summary keeps its status-based fallback until score columns or
    JSON metric fields are added by a future migration.
    """

    direct = _score_to_percent(row.get("score_weighted_composite"))
    if direct is not None:
        return direct
    for field_name in ("metrics", "eval_metrics", "score_metrics"):
        raw = row.get(field_name)
        if not raw:
            continue
        payload: Any
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            payload = raw
        if isinstance(payload, dict):
            nested = _score_to_percent(payload.get("score_weighted_composite"))
            if nested is not None:
                return nested
            expected = payload.get("expected_output")
            if isinstance(expected, dict):
                nested = _score_to_percent(expected.get("score_weighted_composite"))
                if nested is not None:
                    return nested
    return None


def _score_average(rows: list[dict[str, Any]], *, require_complete: bool = False) -> int | None:
    scores = [_extract_weighted_score(row) for row in rows]
    available = [score for score in scores if score is not None]
    if require_complete and len(available) != len(rows):
        return None
    if not available:
        return None
    return round(sum(available) / len(available))


def _status_pass_rate(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    succ = sum(1 for r in rows if r["status"] == "succeeded")
    return round(100.0 * succ / len(rows))


def summary() -> dict[str, Any]:
    logger.debug("evaluate.summary: starting computation")
    stories = corpus.list_stories()
    logger.debug("evaluate.summary: %d story(ies) to evaluate", len(stories))
    rows: list[dict[str, Any]] = []
    total_runs = 0
    pass_rates: list[float] = []
    regressions = 0
    cost_total = 0.0
    cost_n = 0

    for s in stories:
        all_runs = db.list_jobs(change_id=s["id"], run_kind="evaluation", limit=500)
        total_runs += len(all_runs)
        if not all_runs:
            logger.debug("evaluate.summary: %s has no runs", s["id"])
            rows.append({
                "task": s["id"],
                "title": s["title"],
                "baseline": None,
                "current": None,
                "delta": None,
                "runs": 0,
                "status": "no-data",
            })
            continue
        # Baseline = pass rate over the older half of runs (oldest first).
        ordered = sorted(all_runs, key=lambda r: r["submitted_at"])
        if len(ordered) >= 4:
            half = len(ordered) // 2
            baseline_score = _score_average(ordered[:half], require_complete=True)
            current_score = _score_average(all_runs, require_complete=True)
            if baseline_score is not None and current_score is not None:
                baseline = baseline_score
                current = current_score
                score_source = "score_weighted_composite"
            else:
                baseline = _status_pass_rate(ordered[:half])
                current = _status_pass_rate(all_runs)
                score_source = "job_status"
        else:
            current = _score_average(all_runs, require_complete=True)
            score_source = "score_weighted_composite" if current is not None else "job_status"
            if current is None:
                current = _status_pass_rate(all_runs)
            baseline = current
        delta = current - baseline
        if delta <= -10:
            status = "regression"
            regressions += 1
            logger.warning("evaluate.summary: REGRESSION detected for %s (delta=%d%%)", s["id"], delta)
        elif delta < 0:
            status = "marginal"
            logger.debug("evaluate.summary: marginal result for %s (delta=%d%%)", s["id"], delta)
        else:
            status = "pass"
            logger.debug("evaluate.summary: pass for %s (current=%d%% delta=%d%%)", s["id"], current, delta)
        pass_rates.append(current)
        rows.append({
            "task": s["id"],
            "title": s["title"],
            "baseline": baseline,
            "current": current,
            "delta": delta,
            "runs": len(all_runs),
            "status": status,
            "score_source": score_source,
        })
        # Cost aggregation
        for r in all_runs:
            if r.get("cost_usd"):
                cost_total += float(r["cost_usd"] or 0.0)
                cost_n += 1

    overall = round(sum(pass_rates) / len(pass_rates)) if pass_rates else None
    avg_cost = round(cost_total / cost_n, 4) if cost_n else None
    logger.info(
        "evaluate.summary: overall_pass_rate=%s regressions=%d total_runs=%d avg_cost=%s",
        overall, regressions, total_runs, avg_cost,
    )
    return {
        "overall_pass_rate": overall,
        "regressions": regressions,
        "total_runs": total_runs,
        "avg_cost_usd": avg_cost,
        "rows": rows,
    }
