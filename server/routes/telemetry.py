"""Historical run telemetry API."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..telemetry_analysis import build_chart_payload, build_run_profile

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

MAX_ALL_RUNS = 5000
KNOWN_STAGES = (
    "materialize",
    "intake",
    "task-generation",
    "task-assignment",
    "execution",
    "qa",
    "lessons-optimizer",
)
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class TelemetryFilters(BaseModel):
    status: list[str] = Field(default_factory=list)
    runner: list[str] = Field(default_factory=list)
    model: list[str] = Field(default_factory=list)
    run_kind: list[str] = Field(default_factory=list)
    mode: list[str] = Field(default_factory=list)
    repo: list[str] = Field(default_factory=list)
    change_id: str = ""
    submitted_after: str = ""
    submitted_before: str = ""
    q: str = ""
    stage: list[str] = Field(default_factory=list)
    failed_stage: list[str] = Field(default_factory=list)
    min_tokens: int | None = None
    min_cost_usd: float | None = None
    errors_only: bool = False
    awaiting_input_only: bool = False
    missing_events_only: bool = False


class TelemetryQuery(BaseModel):
    selection_mode: Literal["all", "selected"] = "all"
    job_ids: list[str] = Field(default_factory=list)
    filters: TelemetryFilters = Field(default_factory=TelemetryFilters)
    bucket: Literal["hour", "day", "week"] = "day"
    rollup: Literal["run", "story", "parent"] = "run"


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _runtime_seconds(job: dict[str, Any], now: datetime | None = None) -> float | None:
    if job.get("finished_at"):
        return _seconds_between(job.get("started_at"), job.get("finished_at"))
    if job.get("status") not in TERMINAL_STATUSES:
        started = _parse_ts(job.get("started_at"))
        if started is not None:
            return max(0.0, ((now or _now()) - started).total_seconds())
    return None


def _queue_seconds(job: dict[str, Any]) -> float | None:
    return _seconds_between(job.get("submitted_at"), job.get("started_at"))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _estimate_cost(tokens_in: Any, tokens_out: Any) -> float:
    return (max(0, int(tokens_in or 0)) / 1_000_000 * 3.0) + (max(0, int(tokens_out or 0)) / 1_000_000 * 15.0)


def _cost_source(job: dict[str, Any]) -> str:
    cost = float(job.get("cost_usd") or 0.0)
    tokens = int(job.get("tokens_in") or 0) + int(job.get("tokens_out") or 0)
    if cost > 0:
        return "actual"
    if tokens > 0:
        return "estimated"
    return "unknown"


def _display_cost(job: dict[str, Any]) -> float:
    cost = float(job.get("cost_usd") or 0.0)
    if cost > 0:
        return cost
    return _estimate_cost(job.get("tokens_in"), job.get("tokens_out"))


def _job_row(job: dict[str, Any], events: list[dict] | None = None, now: datetime | None = None) -> dict[str, Any]:
    events = events or []
    user_prompts = sum(1 for ev in events if ev.get("type") == "user.prompt")
    failed_stage = _failed_stage(job, events)
    return {
        "id": job.get("id"),
        "change_id": job.get("change_id"),
        "status": job.get("status"),
        "run_kind": job.get("run_kind"),
        "mode": job.get("mode"),
        "runner": job.get("runner"),
        "model": job.get("model"),
        "repo": job.get("repo"),
        "submitted_at": job.get("submitted_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "duration_seconds": _runtime_seconds(job, now),
        "queue_seconds": _queue_seconds(job),
        "tokens_in": int(job.get("tokens_in") or 0),
        "tokens_out": int(job.get("tokens_out") or 0),
        "cost_usd": float(job.get("cost_usd") or 0.0),
        "estimated_cost_usd": _estimate_cost(job.get("tokens_in"), job.get("tokens_out")),
        "cost_source": _cost_source(job),
        "current_stage": job.get("current_stage"),
        "failed_stage": failed_stage,
        "error_message": job.get("error_message"),
        "user_prompts": user_prompts,
        "error_summary": _sample_error_message(job, events),
        "coverage_source": "sqlite" if events else "none",
    }


def _sql_filters(filters: TelemetryFilters | None = None, **kwargs: Any) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add_equal(field: str, value: Any) -> None:
        if value:
            clauses.append(f"{field}=?")
            params.append(value)

    def add_in(field: str, values: list[str]) -> None:
        clean = [v for v in values if v]
        if clean:
            placeholders = ",".join("?" for _ in clean)
            clauses.append(f"{field} IN ({placeholders})")
            params.extend(clean)

    if filters is None:
        add_equal("status", kwargs.get("status"))
        add_equal("runner", kwargs.get("runner"))
        add_equal("model", kwargs.get("model"))
        add_equal("run_kind", kwargs.get("run_kind"))
        add_equal("mode", kwargs.get("mode"))
        add_equal("repo", kwargs.get("repo"))
        add_equal("change_id", kwargs.get("change_id"))
        submitted_after = kwargs.get("submitted_after")
        submitted_before = kwargs.get("submitted_before")
        q = kwargs.get("q")
    else:
        add_in("status", filters.status)
        add_in("runner", filters.runner)
        add_in("model", filters.model)
        add_in("run_kind", filters.run_kind)
        add_in("mode", filters.mode)
        add_in("repo", filters.repo)
        if filters.change_id:
            add_equal("change_id", filters.change_id)
        submitted_after = filters.submitted_after
        submitted_before = filters.submitted_before
        q = filters.q

    if submitted_after:
        clauses.append("submitted_at>=?")
        params.append(submitted_after)
    if submitted_before:
        clauses.append("submitted_at<=?")
        params.append(submitted_before)
    if q:
        like = f"%{q}%"
        clauses.append("(id LIKE ? OR change_id LIKE ? OR repo LIKE ? OR runner LIKE ? OR model LIKE ? OR error_message LIKE ?)")
        params.extend([like] * 6)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def _fetch_jobs(where: str, params: list[Any], *, limit: int, offset: int = 0) -> tuple[int, list[dict]]:
    with db.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params)
        (count,) = cur.fetchone()
        cur.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY submitted_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = [dict(row) for row in cur.fetchall()]
    return int(count), rows


def _fetch_jobs_by_ids(job_ids: list[str]) -> list[dict]:
    clean_ids = [jid for jid in job_ids if jid]
    if not clean_ids:
        return []
    placeholders = ",".join("?" for _ in clean_ids)
    with db.cursor() as cur:
        cur.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", clean_ids)
        by_id = {row["id"]: dict(row) for row in cur.fetchall()}
    return [by_id[jid] for jid in clean_ids if jid in by_id]


def _filter_options() -> dict[str, list[str]]:
    mapping = {
        "statuses": "status",
        "runners": "runner",
        "models": "model",
        "run_kinds": "run_kind",
        "modes": "mode",
        "repos": "repo",
    }
    out: dict[str, list[str]] = {}
    with db.cursor() as cur:
        for key, column in mapping.items():
            cur.execute(f"SELECT DISTINCT {column} FROM jobs WHERE {column} IS NOT NULL AND {column}!='' ORDER BY {column}")
            out[key] = [str(row[0]) for row in cur.fetchall()]
    return out


@router.get("/runs")
async def telemetry_runs(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    runner: str | None = None,
    model: str | None = None,
    run_kind: str | None = None,
    mode: str | None = None,
    repo: str | None = None,
    change_id: str | None = None,
    submitted_after: str | None = None,
    submitted_before: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    where, params = _sql_filters(
        None,
        status=status,
        runner=runner,
        model=model,
        run_kind=run_kind,
        mode=mode,
        repo=repo,
        change_id=change_id,
        submitted_after=submitted_after,
        submitted_before=submitted_before,
        q=q,
    )
    count, jobs = _fetch_jobs(where, params, limit=limit, offset=offset)
    now = _now()
    return {
        "items": [_job_row(job, now=now) for job in jobs],
        "count": count,
        "filter_options": _filter_options(),
    }


@router.post("/query")
async def telemetry_query(body: TelemetryQuery) -> dict[str, Any]:
    filters = body.filters
    truncated = False
    if body.selection_mode == "selected":
        matched_jobs = len([jid for jid in body.job_ids if jid])
        jobs = _fetch_jobs_by_ids(body.job_ids)
    else:
        where, params = _sql_filters(filters)
        db_matched_count, candidate_jobs = _fetch_jobs(where, params, limit=MAX_ALL_RUNS + 1, offset=0)
        missing_candidates = [job for job in candidate_jobs if db.count_telemetry_events(str(job.get("id"))) == 0]
        db.backfill_telemetry_events_for_jobs(missing_candidates)
        candidate_events = db.list_telemetry_events([str(job["id"]) for job in candidate_jobs])
        candidate_events_by_job: dict[str, list[dict]] = defaultdict(list)
        for event in candidate_events:
            if event.get("job_id"):
                candidate_events_by_job[str(event["job_id"])].append(event)
        matched = _apply_post_filters(candidate_jobs, candidate_events_by_job, filters, body.selection_mode)
        has_post_filters = bool(
            filters.stage or filters.failed_stage or filters.min_tokens is not None
            or filters.min_cost_usd is not None or filters.errors_only or filters.awaiting_input_only
            or filters.missing_events_only
        )
        matched_jobs = len(matched) if has_post_filters else db_matched_count
        truncated = matched_jobs > MAX_ALL_RUNS
        jobs = matched[:MAX_ALL_RUNS]

    missing_before = [job for job in jobs if db.count_telemetry_events(str(job.get("id"))) == 0]
    db.backfill_telemetry_events_for_jobs(missing_before)
    events = db.list_telemetry_events([str(job["id"]) for job in jobs])
    events_by_job: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event.get("job_id"):
            events_by_job[str(event["job_id"])].append(event)

    jobs = _apply_post_filters(jobs, events_by_job, filters, body.selection_mode)
    now = _now()
    run_rows = [_job_row(job, events_by_job.get(str(job["id"]), []), now=now) for job in jobs]
    coverage = _coverage(jobs, events_by_job, truncated=truncated, matched_jobs=matched_jobs)
    user_input_stats = _user_input_stats(jobs, events_by_job, now)
    summary = _summary(jobs, events_by_job, user_input_stats, now)
    profiles = [build_run_profile(job, events_by_job.get(str(job["id"]), []), now) for job in jobs]
    return {
        "selected_count": len(jobs),
        "coverage": coverage,
        "summary": summary,
        "stage_stats": _stage_stats(jobs, events_by_job, now),
        "error_stats": _error_stats(jobs, events_by_job),
        "user_input_stats": user_input_stats,
        "runner_model_stats": _runner_model_stats(jobs, events_by_job, now),
        "active_runs": _active_runs(jobs, events_by_job, now),
        "run_rows": run_rows,
        "timeseries": _timeseries(jobs),
        "charts": build_chart_payload(profiles, bucket=body.bucket, rollup=body.rollup),
    }


@router.get("/runs/{job_id}/profile")
async def telemetry_run_profile(job_id: str) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if db.count_telemetry_events(job_id) == 0:
        db.backfill_telemetry_events_for_job(job)
    events = db.list_telemetry_events([job_id])
    now = _now()
    return {
        "job": _job_row(job, events, now=now),
        "profile": build_run_profile(job, events, now),
    }


def _apply_post_filters(
    jobs: list[dict],
    events_by_job: dict[str, list[dict]],
    filters: TelemetryFilters,
    selection_mode: str,
) -> list[dict]:
    if selection_mode == "selected":
        return jobs
    out: list[dict] = []
    for job in jobs:
        events = events_by_job.get(str(job.get("id")), [])
        tokens = int(job.get("tokens_in") or 0) + int(job.get("tokens_out") or 0)
        if filters.min_tokens is not None and tokens < filters.min_tokens:
            continue
        if filters.min_cost_usd is not None and _display_cost(job) < filters.min_cost_usd:
            continue
        if filters.awaiting_input_only and job.get("status") != "awaiting_input":
            continue
        if filters.missing_events_only and events:
            continue
        if filters.errors_only and not _sample_error_message(job, events):
            continue
        if filters.stage and not ({ev.get("stage") for ev in events if ev.get("stage")} & set(filters.stage)):
            continue
        if filters.failed_stage:
            failed = _failed_stage(job, events)
            if failed not in set(filters.failed_stage):
                continue
        out.append(job)
    return out


def _coverage(jobs: list[dict], events_by_job: dict[str, list[dict]], *, truncated: bool, matched_jobs: int) -> dict[str, Any]:
    jobs_with_events = sum(1 for job in jobs if events_by_job.get(str(job.get("id"))))
    token_data = sum(1 for job in jobs if int(job.get("tokens_in") or 0) + int(job.get("tokens_out") or 0) > 0)
    cost_sources = [_cost_source(job) for job in jobs]
    actual = sum(1 for source in cost_sources if source == "actual")
    estimated = sum(1 for source in cost_sources if source == "estimated")
    if actual and estimated:
        cost_data_source = "mixed"
    elif actual:
        cost_data_source = "actual"
    elif estimated:
        cost_data_source = "estimated"
    else:
        cost_data_source = "unknown"
    return {
        "jobs_with_events": jobs_with_events,
        "jobs_missing_events": max(0, len(jobs) - jobs_with_events),
        "token_data_available": token_data,
        "cost_data_available": actual + estimated,
        "cost_is_partially_estimated": estimated > 0,
        "cost_data_source": cost_data_source,
        "truncated": truncated,
        "matched_jobs": matched_jobs,
        "aggregated_jobs": len(jobs),
    }


def _summary(jobs: list[dict], events_by_job: dict[str, list[dict]], user_input_stats: dict[str, Any], now: datetime) -> dict[str, Any]:
    statuses = Counter(str(job.get("status") or "") for job in jobs)
    runtimes = [value for job in jobs if (value := _runtime_seconds(job, now)) is not None]
    queues = [value for job in jobs if (value := _queue_seconds(job)) is not None]
    failed_stages = Counter(_failed_stage(job, events_by_job.get(str(job.get("id")), [])) for job in jobs)
    failed_stages.pop(None, None)
    tokens_in = sum(int(job.get("tokens_in") or 0) for job in jobs)
    tokens_out = sum(int(job.get("tokens_out") or 0) for job in jobs)
    cost_actual = sum(float(job.get("cost_usd") or 0.0) for job in jobs)
    cost_estimated = sum(_display_cost(job) for job in jobs)
    total = len(jobs) or 1
    return {
        "active": statuses.get("running", 0),
        "queued": statuses.get("queued", 0),
        "awaiting_input": statuses.get("awaiting_input", 0),
        "succeeded": statuses.get("succeeded", 0),
        "failed": statuses.get("failed", 0),
        "cancelled": statuses.get("cancelled", 0),
        "success_rate": round(statuses.get("succeeded", 0) / total, 4),
        "failure_rate": round(statuses.get("failed", 0) / total, 4),
        "median_runtime_seconds": _percentile(runtimes, 50),
        "p95_runtime_seconds": _percentile(runtimes, 95),
        "median_queue_seconds": _percentile(queues, 50),
        "p95_queue_seconds": _percentile(queues, 95),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cost_usd": round(cost_actual, 6),
        "estimated_cost_usd": round(cost_estimated, 6),
        "top_failed_stage": failed_stages.most_common(1)[0][0] if failed_stages else None,
        "user_blocked_seconds": user_input_stats.get("total_blocked_seconds", 0),
    }


def _stage_stats(jobs: list[dict], events_by_job: dict[str, list[dict]], now: datetime) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        stage: {"stage": stage, "durations": [], "active_count": 0, "failure_count": 0}
        for stage in KNOWN_STAGES
    }
    for job in jobs:
        starts: dict[str, datetime] = {}
        for ev in events_by_job.get(str(job.get("id")), []):
            stage = ev.get("stage")
            if not stage:
                continue
            stats.setdefault(stage, {"stage": stage, "durations": [], "active_count": 0, "failure_count": 0})
            if ev.get("type") == "stage.start":
                ts = _parse_ts(ev.get("ts"))
                if ts is not None:
                    starts[stage] = ts
            elif ev.get("type") == "stage.end":
                end = _parse_ts(ev.get("ts"))
                start = starts.pop(stage, None)
                if start is not None and end is not None:
                    stats[stage]["durations"].append(max(0.0, (end - start).total_seconds()))
                if ev.get("status") == "error":
                    stats[stage]["failure_count"] += 1
        if job.get("status") in ("running", "awaiting_input") and job.get("current_stage") in starts:
            stage = job["current_stage"]
            stats[stage]["durations"].append(max(0.0, (now - starts[stage]).total_seconds()))
            stats[stage]["active_count"] += 1
    rows: list[dict[str, Any]] = []
    for stage, item in stats.items():
        durations = item["durations"]
        count = len(durations)
        failure_count = item["failure_count"]
        rows.append({
            "stage": stage,
            "runs_observed": count,
            "active_count": item["active_count"],
            "average_duration_seconds": round(sum(durations) / count, 3) if count else None,
            "median_duration_seconds": _percentile(durations, 50),
            "p95_duration_seconds": _percentile(durations, 95),
            "p99_duration_seconds": _percentile(durations, 99),
            "total_duration_seconds": round(sum(durations), 3),
            "failure_count": failure_count,
            "failure_rate": round(failure_count / count, 4) if count else 0,
        })
    return rows


def _normalize_signature(message: Any) -> str:
    text = str(message or "").strip().lower()
    text = re.sub(r"/[^\s:]+", "<path>", text)
    text = re.sub(r"\bjob_[0-9a-f]+\b", "<job>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ][0-9:.+-]+z?\b", "<ts>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    return re.sub(r"\s+", " ", text)[:180] or "unknown error"


def _error_events(job: dict[str, Any], events: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if job.get("error_message"):
        out.append({"message": job["error_message"], "stage": job.get("current_stage"), "ts": job.get("finished_at") or job.get("submitted_at")})
    for ev in events:
        message = ev.get("message") or ev.get("msg") or ev.get("error") or ev.get("title")
        if ev.get("type") == "log" and ev.get("level") == "error":
            out.append({"message": message, "stage": ev.get("stage"), "ts": ev.get("ts")})
        elif ev.get("type") == "stage.end" and ev.get("status") == "error":
            out.append({"message": message or f"{ev.get('stage')} failed", "stage": ev.get("stage"), "ts": ev.get("ts")})
        elif ev.get("type") == "cli.exit" and int(ev.get("exit_code") or 0) != 0:
            out.append({"message": message or f"cli exit {ev.get('exit_code')}", "stage": ev.get("stage"), "ts": ev.get("ts")})
        elif ev.get("type") == "opik.end" and ev.get("status") == "error":
            out.append({"message": message or "opik span failed", "stage": ev.get("stage"), "ts": ev.get("ts")})
        elif ev.get("type") == "user.prompt.timeout":
            out.append({"message": message or "user prompt timeout", "stage": ev.get("stage"), "ts": ev.get("ts")})
    return out


def _error_severity(signature: str, message: str, count: int) -> tuple[str, int]:
    text = f"{signature} {message}".lower()
    if count >= 10 or "out of memory" in text or "permission denied" in text:
        return "critical", 4
    if count >= 3 or "timeout" in text or "rate limit" in text or "failed" in text:
        return "high", 3
    if count >= 1:
        return "medium", 2
    return "low", 1


def _error_stats(jobs: list[dict], events_by_job: dict[str, list[dict]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    selected_ids = {str(job.get("id")) for job in jobs}
    for job in jobs:
        events = events_by_job.get(str(job.get("id")), [])
        for error in _error_events(job, events):
            signature = _normalize_signature(error.get("message"))
            item = grouped.setdefault(signature, {
                "signature": signature,
                "count": 0,
                "last_seen": None,
                "affected_stage": error.get("stage"),
                "runner_model": f"{job.get('runner') or ''}/{job.get('model') or ''}".strip("/"),
                "sample_job_id": job.get("id"),
                "sample_change_id": job.get("change_id"),
                "sample_message": str(error.get("message") or "")[:500],
                "recurring": False,
                "severity": "low",
                "severity_score": 1,
                "triage_state": "open",
            })
            item["count"] += 1
            item["recurring"] = item["count"] > 1
            severity, severity_score = _error_severity(signature, str(item.get("sample_message") or ""), int(item["count"]))
            item["severity"] = severity
            item["severity_score"] = severity_score
            if str(job.get("id")) in selected_ids:
                ts = error.get("ts")
                if ts and (item["last_seen"] is None or str(ts) > str(item["last_seen"])):
                    item["last_seen"] = ts
    def sort_key(row: dict[str, Any]) -> tuple[int, int, float]:
        seen = _parse_ts(row.get("last_seen"))
        seen_ts = seen.timestamp() if seen is not None else 0.0
        return (-int(row.get("severity_score") or 0), -int(row.get("count") or 0), -seen_ts)

    return sorted(grouped.values(), key=sort_key)[:100]


def _sample_error_message(job: dict[str, Any], events: list[dict]) -> str | None:
    errors = _error_events(job, events)
    return str(errors[-1]["message"])[:500] if errors else None


def _failed_stage(job: dict[str, Any], events: list[dict]) -> str | None:
    for ev in reversed(events):
        if ev.get("type") == "stage.end" and ev.get("status") == "error" and ev.get("stage"):
            return str(ev["stage"])
    return None


def _user_input_stats(jobs: list[dict], events_by_job: dict[str, list[dict]], now: datetime) -> dict[str, Any]:
    prompt_count = 0
    timeout_count = 0
    blocked: list[float] = []
    active_prompts: list[dict[str, Any]] = []
    stage_counts: Counter[str] = Counter()
    runs_needing_input = sum(1 for job in jobs if job.get("status") == "awaiting_input")
    for job in jobs:
        unresolved: dict[str, dict[str, Any]] = {}
        for index, ev in enumerate(events_by_job.get(str(job.get("id")), [])):
            key = str(ev.get("escalation_id") or ev.get("conversation_id") or index)
            if ev.get("type") == "user.prompt":
                prompt_count += 1
                if ev.get("stage"):
                    stage_counts[str(ev["stage"])] += 1
                unresolved[key] = ev
            elif ev.get("type") in ("user.response", "user.escalation.resolved", "user.prompt.timeout"):
                if ev.get("type") == "user.prompt.timeout":
                    timeout_count += 1
                prompt = unresolved.pop(key, None)
                start = _parse_ts(prompt.get("ts")) if prompt else None
                end = _parse_ts(ev.get("ts"))
                if start is not None and end is not None:
                    blocked.append(max(0.0, (end - start).total_seconds()))
        for prompt in unresolved.values():
            start = _parse_ts(prompt.get("ts"))
            age = max(0.0, (now - start).total_seconds()) if start is not None else None
            if age is not None:
                blocked.append(age)
            active_prompts.append({
                "job_id": job.get("id"),
                "change_id": job.get("change_id"),
                "stage": prompt.get("stage"),
                "agent": prompt.get("agent"),
                "severity": prompt.get("severity"),
                "title": prompt.get("title") or prompt.get("message"),
                "age_seconds": age,
            })
    total_blocked = round(sum(blocked), 3)
    return {
        "runs_needing_input": runs_needing_input,
        "total_prompt_count": prompt_count,
        "timeout_count": timeout_count,
        "unresolved_active_prompt_count": len(active_prompts),
        "total_blocked_seconds": total_blocked,
        "average_blocked_seconds": round(total_blocked / len(blocked), 3) if blocked else 0,
        "most_common_prompting_stage": stage_counts.most_common(1)[0][0] if stage_counts else None,
        "active_unresolved_prompts": active_prompts,
    }


def _runner_model_stats(jobs: list[dict], events_by_job: dict[str, list[dict]], now: datetime) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for job in jobs:
        grouped[(str(job.get("runner") or ""), str(job.get("model") or ""))].append(job)
    rows: list[dict[str, Any]] = []
    for (runner, model), group in grouped.items():
        runtimes = [value for job in group if (value := _runtime_seconds(job, now)) is not None]
        successes = sum(1 for job in group if job.get("status") == "succeeded")
        failures = sum(1 for job in group if job.get("status") == "failed")
        tokens = sum(int(job.get("tokens_in") or 0) + int(job.get("tokens_out") or 0) for job in group)
        cost = sum(_display_cost(job) for job in group)
        prompts = sum(
            1 for job in group for ev in events_by_job.get(str(job.get("id")), []) if ev.get("type") == "user.prompt"
        )
        common_failure = Counter(
            _normalize_signature(_sample_error_message(job, events_by_job.get(str(job.get("id")), [])))
            for job in group
            if _sample_error_message(job, events_by_job.get(str(job.get("id")), []))
        )
        total = len(group) or 1
        rows.append({
            "runner": runner,
            "model": model,
            "runner_model": f"{runner}/{model}".strip("/"),
            "runs": len(group),
            "success_rate": round(successes / total, 4),
            "median_runtime_seconds": _percentile(runtimes, 50),
            "p95_runtime_seconds": _percentile(runtimes, 95),
            "tokens_per_run": round(tokens / total, 3),
            "cost_per_run": round(cost / total, 6),
            "cost_per_successful_run": round(cost / successes, 6) if successes else None,
            "failure_rate": round(failures / total, 4),
            "most_common_failure": common_failure.most_common(1)[0][0] if common_failure else None,
            "user_prompts_per_run": round(prompts / total, 3),
        })
    return sorted(rows, key=lambda row: row["runs"], reverse=True)


def _active_runs(jobs: list[dict], events_by_job: dict[str, list[dict]], now: datetime) -> list[dict[str, Any]]:
    rows = []
    for job in jobs:
        if job.get("status") not in ("queued", "running", "awaiting_input"):
            continue
        row = _job_row(job, events_by_job.get(str(job.get("id")), []), now=now)
        badges = []
        if job.get("status") == "awaiting_input":
            badges.append("awaiting user")
        if (row.get("tokens_in") or 0) + (row.get("tokens_out") or 0) >= 100_000:
            badges.append("high token use")
        queue = row.get("queue_seconds")
        if job.get("status") == "queued" and queue is not None and queue > 300:
            badges.append("queued too long")
        row["attention_badges"] = badges
        rows.append(row)
    return rows


def _timeseries(jobs: list[dict]) -> list[dict[str, Any]]:
    submitted = [_parse_ts(job.get("submitted_at")) for job in jobs]
    submitted = [ts for ts in submitted if ts is not None]
    if not submitted:
        return []
    span = max(submitted) - min(submitted)
    hourly = span.total_seconds() <= 60 * 60 * 48
    buckets: dict[str, dict[str, Any]] = {}
    for job in jobs:
        ts = _parse_ts(job.get("submitted_at"))
        if ts is None:
            continue
        bucket_ts = ts.replace(minute=0, second=0, microsecond=0) if hourly else ts.replace(hour=0, minute=0, second=0, microsecond=0)
        key = bucket_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        bucket = buckets.setdefault(key, {
            "bucket": key,
            "submitted": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "tokens_total": 0,
            "cost_total": 0.0,
        })
        bucket["submitted"] += 1
        if job.get("status") in ("succeeded", "failed", "cancelled"):
            bucket[str(job["status"])] += 1
        bucket["tokens_total"] += int(job.get("tokens_in") or 0) + int(job.get("tokens_out") or 0)
        bucket["cost_total"] = round(float(bucket["cost_total"]) + _display_cost(job), 6)
    return [buckets[key] for key in sorted(buckets)]
