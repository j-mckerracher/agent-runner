"""Evaluation metrics aggregator for the Evaluate view."""
from __future__ import annotations

from typing import Any

from server.db import get_conn


def get_evaluate_summary() -> dict[str, Any]:
    """Aggregate pass rates, regressions, and per-change_id bars from jobs table."""
    try:
        conn = get_conn()

        # Overall stats
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        status_counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
        total = sum(status_counts.values())
        succeeded = status_counts.get("succeeded", 0)
        failed = status_counts.get("failed", 0)
        overall_pass_rate = (succeeded / total) if total > 0 else None

        # Per-change_id summary
        per_change = conn.execute(
            """SELECT change_id,
                      COUNT(*) as total_runs,
                      SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) as passed,
                      SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed_count,
                      MAX(submitted_at) as last_run
               FROM jobs
               GROUP BY change_id
               ORDER BY last_run DESC"""
        ).fetchall()

        per_change_list: list[dict[str, Any]] = []
        for r in per_change:
            total_r = r["total_runs"]
            passed_r = r["passed"]
            per_change_list.append({
                "change_id": r["change_id"],
                "total_runs": total_r,
                "passed": passed_r,
                "failed": r["failed_count"],
                "pass_rate": (passed_r / total_r) if total_r > 0 else None,
                "last_run": r["last_run"],
            })

        # Detect regressions: change_ids where last run failed but prior run succeeded
        regressions: list[dict[str, Any]] = []
        for r in per_change:
            change_id = r["change_id"]
            last_two = conn.execute(
                """SELECT status FROM jobs WHERE change_id = ?
                   ORDER BY submitted_at DESC LIMIT 2""",
                (change_id,),
            ).fetchall()
            if len(last_two) >= 2:
                if last_two[0]["status"] == "failed" and last_two[1]["status"] == "succeeded":
                    regressions.append({"change_id": change_id})

        return {
            "total_runs": total,
            "succeeded": succeeded,
            "failed": failed,
            "overall_pass_rate": overall_pass_rate,
            "per_change": per_change_list,
            "regressions": regressions,
        }
    except Exception as exc:
        return {
            "total_runs": 0,
            "succeeded": 0,
            "failed": 0,
            "overall_pass_rate": None,
            "per_change": [],
            "regressions": [],
            "error": str(exc),
        }
