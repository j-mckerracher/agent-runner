"""Aggregate metrics for the Evaluate view."""
from __future__ import annotations

import logging
from typing import Any

from . import db, corpus

logger = logging.getLogger(__name__)


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
        succ = sum(1 for r in all_runs if r["status"] == "succeeded")
        current = round(100.0 * succ / len(all_runs))
        # Baseline = pass rate over the older half of runs (oldest first).
        ordered = sorted(all_runs, key=lambda r: r["submitted_at"])
        if len(ordered) >= 4:
            half = len(ordered) // 2
            base_succ = sum(1 for r in ordered[:half] if r["status"] == "succeeded")
            baseline = round(100.0 * base_succ / half)
        else:
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
