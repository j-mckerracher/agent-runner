"""Reusable telemetry profile and chart derivation helpers."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
Bucket = Literal["hour", "day", "week"]
Rollup = Literal["run", "story", "parent"]


def parse_ts(value: Any) -> datetime | None:
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


def iso_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def seconds_between(start: Any, end: Any) -> float | None:
    start_dt = parse_ts(start)
    end_dt = parse_ts(end)
    if start_dt is None or end_dt is None:
        return None
    return round(max(0.0, (end_dt - start_dt).total_seconds()), 3)


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percentile_value / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def distribution_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "min_seconds": None,
            "q1_seconds": None,
            "median_seconds": None,
            "q3_seconds": None,
            "max_seconds": None,
            "p95_seconds": None,
            "p99_seconds": None,
            "mean_seconds": None,
        }
    ordered = sorted(float(value) for value in values)
    return {
        "min_seconds": round(ordered[0], 3),
        "q1_seconds": percentile(ordered, 25),
        "median_seconds": percentile(ordered, 50),
        "q3_seconds": percentile(ordered, 75),
        "max_seconds": round(ordered[-1], 3),
        "p95_seconds": percentile(ordered, 95),
        "p99_seconds": percentile(ordered, 99),
        "mean_seconds": round(sum(ordered) / len(ordered), 3),
    }


def estimate_cost(tokens_in: Any, tokens_out: Any) -> float:
    return round(
        (max(0, int(tokens_in or 0)) / 1_000_000 * 3.0)
        + (max(0, int(tokens_out or 0)) / 1_000_000 * 15.0),
        6,
    )


def display_cost(cost_usd: Any, tokens_in: Any, tokens_out: Any) -> float:
    cost = float(cost_usd or 0.0)
    return round(cost, 6) if cost > 0 else estimate_cost(tokens_in, tokens_out)


def _event_sort_key(event: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(event.get("job_id") or ""),
        int(event.get("seq") or 0),
        str(event.get("ts") or ""),
    )


def _duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 3)


def bucket_timestamp(value: Any, bucket: Bucket = "day") -> str | None:
    ts = parse_ts(value)
    if ts is None:
        return None
    if bucket == "hour":
        bucketed = ts.replace(minute=0, second=0, microsecond=0)
    elif bucket == "week":
        start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        bucketed = start.replace(day=start.day) - _weekday_delta(start)
    else:
        bucketed = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return iso_ts(bucketed)


def _weekday_delta(value: datetime):
    from datetime import timedelta

    return timedelta(days=value.weekday())


def build_stage_spans(job: dict[str, Any], events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    open_by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    spans: list[dict[str, Any]] = []
    for event in sorted(events, key=_event_sort_key):
        stage = event.get("stage")
        if not stage:
            continue
        event_type = event.get("type")
        ts = parse_ts(event.get("ts"))
        if event_type == "stage.start":
            open_by_stage[str(stage)].append({
                "stage": str(stage),
                "start_ts": ts,
                "end_ts": None,
                "status": None,
                "active": False,
                "inferred_start": False,
                "inferred_end": False,
            })
        elif event_type == "stage.end":
            stack = open_by_stage[str(stage)]
            span = stack.pop() if stack else {
                "stage": str(stage),
                "start_ts": None,
                "end_ts": None,
                "status": None,
                "active": False,
                "inferred_start": True,
                "inferred_end": False,
            }
            span["end_ts"] = ts
            span["status"] = event.get("status") or "ok"
            span["duration_seconds"] = _duration_seconds(span.get("start_ts"), span.get("end_ts"))
            spans.append(span)

    finished = parse_ts(job.get("finished_at"))
    active_cutoff = now if job.get("status") not in TERMINAL_STATUSES else finished
    for stage, stack in open_by_stage.items():
        for span in stack:
            span["end_ts"] = active_cutoff
            span["status"] = span.get("status") or ("active" if job.get("status") not in TERMINAL_STATUSES else "unknown")
            span["active"] = job.get("status") not in TERMINAL_STATUSES
            span["inferred_end"] = True
            span["duration_seconds"] = _duration_seconds(span.get("start_ts"), active_cutoff)
            span["stage"] = stage
            spans.append(span)

    for span in spans:
        span["start_ts"] = iso_ts(span.get("start_ts"))
        span["end_ts"] = iso_ts(span.get("end_ts"))
        span.setdefault("duration_seconds", _duration_seconds(parse_ts(span.get("start_ts")), parse_ts(span.get("end_ts"))))
    return sorted(spans, key=lambda item: (str(item.get("start_ts") or ""), str(item.get("stage") or "")))


def build_user_wait_spans(events: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    unresolved: dict[str, dict[str, Any]] = {}
    spans: list[dict[str, Any]] = []
    for index, event in enumerate(sorted(events, key=_event_sort_key)):
        key = str(event.get("escalation_id") or event.get("conversation_id") or index)
        if event.get("type") == "user.prompt":
            unresolved[key] = event
        elif event.get("type") in ("user.response", "user.escalation.resolved", "user.prompt.timeout"):
            prompt = unresolved.pop(key, None)
            if prompt is None:
                continue
            start = parse_ts(prompt.get("ts"))
            end = parse_ts(event.get("ts"))
            spans.append({
                "stage": prompt.get("stage") or event.get("stage"),
                "agent": prompt.get("agent") or event.get("agent"),
                "start_ts": iso_ts(start),
                "end_ts": iso_ts(end),
                "duration_seconds": _duration_seconds(start, end),
                "title": prompt.get("title") or prompt.get("message"),
                "status": "timeout" if event.get("type") == "user.prompt.timeout" else "resolved",
            })
    for prompt in unresolved.values():
        start = parse_ts(prompt.get("ts"))
        spans.append({
            "stage": prompt.get("stage"),
            "agent": prompt.get("agent"),
            "start_ts": iso_ts(start),
            "end_ts": iso_ts(now),
            "duration_seconds": _duration_seconds(start, now),
            "title": prompt.get("title") or prompt.get("message"),
            "status": "active",
        })
    return spans


def _tokens_for_event(event: dict[str, Any]) -> tuple[int, int, str]:
    actual_in = event.get("tokens_in")
    actual_out = event.get("tokens_out")
    if actual_in is not None or actual_out is not None:
        return int(actual_in or 0), int(actual_out or 0), "actual"
    est_in = event.get("prompt_est_tokens")
    est_out = event.get("response_est_tokens")
    if est_in is not None or est_out is not None:
        return int(est_in or 0), int(est_out or 0), "estimated"
    return 0, 0, "unknown"


def _stage_at_ts(stage_spans: list[dict[str, Any]], ts_value: Any) -> str | None:
    ts = parse_ts(ts_value)
    if ts is None:
        return None
    for span in stage_spans:
        start = parse_ts(span.get("start_ts"))
        end = parse_ts(span.get("end_ts"))
        if start is not None and end is not None and start <= ts <= end:
            return str(span.get("stage") or "")
    return None


def extract_llm_calls(
    job: dict[str, Any],
    events: list[dict[str, Any]],
    stage_spans: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in sorted(events, key=_event_sort_key):
        if event.get("type") != "llm.call":
            continue
        tokens_in, tokens_out, token_source = _tokens_for_event(event)
        stage = event.get("stage") or _stage_at_ts(stage_spans, event.get("ts")) or "unattributed"
        model = event.get("model") or job.get("model") or "unknown"
        calls.append({
            "seq": event.get("seq"),
            "ts": event.get("ts"),
            "stage": stage,
            "agent": event.get("agent") or "unknown",
            "runner": event.get("runner") or job.get("runner") or "unknown",
            "model": model,
            "duration_ms": event.get("duration_ms") or event.get("latency_ms"),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "token_source": token_source,
            "cost_usd": float(event.get("cost_usd") or 0.0),
            "estimated_cost_usd": display_cost(event.get("cost_usd"), tokens_in, tokens_out),
            "status": event.get("status"),
            "attempt": event.get("attempt"),
            "max_attempts": event.get("max_attempts"),
            "tool_step_count": event.get("tool_step_count"),
            "tool_call_count": event.get("tool_call_count"),
            "error_category": event.get("error_category"),
        })
    return calls


def extract_cli_calls(events: list[dict[str, Any]], stage_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    for event in sorted(events, key=_event_sort_key):
        if event.get("type") != "cli.exit":
            continue
        stage = event.get("stage") or _stage_at_ts(stage_spans, event.get("ts")) or "unattributed"
        calls.append({
            "seq": event.get("seq"),
            "ts": event.get("ts"),
            "stage": stage,
            "agent": event.get("agent"),
            "runner": event.get("runner"),
            "exit_code": event.get("exit_code"),
            "duration_ms": event.get("duration_ms") or event.get("latency_ms"),
            "status": "ok" if int(event.get("exit_code") or 0) == 0 else "error",
        })
    return calls


def extract_loop_instances(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    loops: list[dict[str, Any]] = []
    for event in sorted(events, key=_event_sort_key):
        if event.get("type") == "loop.end":
            loops.append({
                "loop_name": event.get("loop_name") or event.get("name") or "unknown",
                "stage": event.get("stage"),
                "uow_id": event.get("uow_id"),
                "actual_iterations": int(event.get("actual_iterations") or event.get("iteration") or 0),
                "max_iterations": event.get("max_iterations"),
                "passed": event.get("passed"),
                "stopped_early": event.get("stopped_early"),
                "exhausted": event.get("exhausted"),
                "duration_ms": event.get("duration_ms") or event.get("latency_ms"),
                "source": "loop_events",
            })
        elif event.get("type") == "opik.start" and re.search(r"(?:uow|eval|optimizer).*iteration", str(event.get("name") or ""), re.I):
            loops.append({
                "loop_name": event.get("name"),
                "stage": event.get("stage"),
                "uow_id": event.get("uow_id"),
                "actual_iterations": 1,
                "max_iterations": None,
                "passed": None,
                "stopped_early": None,
                "exhausted": None,
                "duration_ms": None,
                "source": "opik_fallback",
            })
    return loops


def _breakdown(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row.get(key) or "unattributed")
        item = grouped.setdefault(label, {
            key: label,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "estimated_cost_usd": 0.0,
            "sources": Counter(),
        })
        item["tokens_in"] += int(row.get("tokens_in") or 0)
        item["tokens_out"] += int(row.get("tokens_out") or 0)
        item["tokens_total"] += int(row.get("tokens_total") or 0)
        item["estimated_cost_usd"] = round(float(item["estimated_cost_usd"]) + float(row.get("estimated_cost_usd") or 0.0), 6)
        item["sources"][str(row.get("token_source") or "unknown")] += 1
    out = []
    for item in grouped.values():
        sources = item.pop("sources")
        item["token_source"] = "mixed" if len(sources) > 1 else (next(iter(sources)) if sources else "unknown")
        out.append(item)
    return sorted(out, key=lambda item: int(item.get("tokens_total") or 0), reverse=True)


def _error_events(job: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    if job.get("error_message"):
        out.append({"message": job.get("error_message"), "stage": job.get("current_stage"), "ts": job.get("finished_at") or job.get("submitted_at")})
    for event in events:
        message = event.get("message") or event.get("msg") or event.get("error") or event.get("title")
        if event.get("type") == "log" and event.get("level") == "error":
            out.append({"message": message, "stage": event.get("stage"), "ts": event.get("ts")})
        elif event.get("type") == "stage.end" and event.get("status") == "error":
            out.append({"message": message or f"{event.get('stage')} failed", "stage": event.get("stage"), "ts": event.get("ts")})
        elif event.get("type") == "cli.exit" and int(event.get("exit_code") or 0) != 0:
            out.append({"message": message or f"cli exit {event.get('exit_code')}", "stage": event.get("stage"), "ts": event.get("ts")})
        elif event.get("type") == "opik.end" and event.get("status") == "error":
            out.append({"message": message or "opik span failed", "stage": event.get("stage"), "ts": event.get("ts")})
        elif event.get("type") == "user.prompt.timeout":
            out.append({"message": message or "user prompt timeout", "stage": event.get("stage"), "ts": event.get("ts")})
    return out


def build_run_profile(job: dict[str, Any], events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    stage_spans = build_stage_spans(job, events, now)
    user_wait_spans = build_user_wait_spans(events, now)
    llm_calls = extract_llm_calls(job, events, stage_spans)
    cli_calls = extract_cli_calls(events, stage_spans)
    loop_instances = extract_loop_instances(events)

    if llm_calls:
        tokens_in = sum(int(call.get("tokens_in") or 0) for call in llm_calls)
        tokens_out = sum(int(call.get("tokens_out") or 0) for call in llm_calls)
        token_source = "mixed" if len({call.get("token_source") for call in llm_calls}) > 1 else str(llm_calls[0].get("token_source") or "unknown")
    else:
        tokens_in = int(job.get("tokens_in") or 0)
        tokens_out = int(job.get("tokens_out") or 0)
        token_source = "job_fallback" if tokens_in or tokens_out else "unknown"

    model_values = [str(call.get("model")) for call in llm_calls if call.get("model")]
    if not model_values and job.get("model"):
        model_values = [str(job["model"])]
    models_used = sorted(set(model_values))
    primary_model = job.get("model") or (models_used[0] if models_used else None)
    stage_duration_totals: Counter[str] = Counter()
    for span in stage_spans:
        stage_duration_totals[str(span.get("stage"))] += float(span.get("duration_seconds") or 0.0)
    token_stage_totals = Counter({
        str(row.get("stage")): int(row.get("tokens_total") or 0)
        for row in _breakdown(llm_calls, "stage")
    })
    errors = _error_events(job, events)
    complete_elapsed_end = job.get("finished_at") or (iso_ts(now) if job.get("status") not in TERMINAL_STATUSES else None)

    return {
        "job_id": job.get("id"),
        "parent_job_id": job.get("parent_job_id"),
        "root_job_id": job.get("parent_job_id") or job.get("id"),
        "change_id": job.get("change_id"),
        "repo": job.get("repo"),
        "run_kind": job.get("run_kind"),
        "status": job.get("status"),
        "submitted_at": job.get("submitted_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "complete_elapsed_seconds": seconds_between(job.get("submitted_at"), complete_elapsed_end),
        "queue_seconds": seconds_between(job.get("submitted_at"), job.get("started_at")),
        "runtime_seconds": seconds_between(job.get("started_at"), job.get("finished_at") or (iso_ts(now) if job.get("started_at") and job.get("status") not in TERMINAL_STATUSES else None)),
        "user_blocked_seconds": round(sum(float(span.get("duration_seconds") or 0.0) for span in user_wait_spans), 3),
        "original_ac_count": job.get("original_ac_count"),
        "normalized_ac_count": job.get("normalized_ac_count"),
        "story_source": job.get("story_source"),
        "runner": job.get("runner"),
        "primary_model": primary_model,
        "models_used": models_used,
        "model_set": ", ".join(models_used) if models_used else "unknown",
        "stage_spans": stage_spans,
        "user_wait_spans": user_wait_spans,
        "llm_calls": llm_calls,
        "cli_calls": cli_calls,
        "loop_instances": loop_instances,
        "tokens": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "source": token_source,
            "estimated_cost_usd": display_cost(job.get("cost_usd"), tokens_in, tokens_out),
        },
        "token_by_stage": _breakdown(llm_calls, "stage"),
        "token_by_model": _breakdown(llm_calls, "model"),
        "token_by_agent": _breakdown(llm_calls, "agent"),
        "dominant_stage_by_time": stage_duration_totals.most_common(1)[0][0] if stage_duration_totals else None,
        "dominant_stage_by_tokens": token_stage_totals.most_common(1)[0][0] if token_stage_totals else None,
        "failed_stage": next((error.get("stage") for error in reversed(errors) if error.get("stage")), None),
        "errors": errors,
        "coverage": {
            "has_events": bool(events),
            "has_llm_calls": bool(llm_calls),
            "has_stage_spans": bool(stage_spans),
            "has_original_ac_count": job.get("original_ac_count") is not None,
            "has_normalized_ac_count": job.get("normalized_ac_count") is not None,
            "has_loop_events": any(loop.get("source") == "loop_events" for loop in loop_instances),
            "loop_source": "loop_events" if any(loop.get("source") == "loop_events" for loop in loop_instances) else ("opik_fallback" if loop_instances else "none"),
            "token_source": token_source,
        },
    }


def _group_profiles_by_bucket(profiles: list[dict[str, Any]], bucket: Bucket) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        key = bucket_timestamp(profile.get("submitted_at"), bucket)
        if key:
            grouped[key].append(profile)
    return grouped


def _token_source_for_profiles(profiles: list[dict[str, Any]]) -> str:
    sources = {str(profile.get("tokens", {}).get("source") or "unknown") for profile in profiles}
    sources.discard("unknown")
    if not sources:
        return "unknown"
    return "mixed" if len(sources) > 1 else next(iter(sources))


def build_chart_payload(
    profiles: list[dict[str, Any]],
    *,
    bucket: Bucket = "day",
    rollup: Rollup = "run",
) -> dict[str, Any]:
    grouped = _group_profiles_by_bucket(profiles, bucket)
    duration_points = []
    ac_points = []
    for profile in profiles:
        tokens = profile.get("tokens") or {}
        loop_iterations = sum(int(loop.get("actual_iterations") or 0) for loop in profile.get("loop_instances") or [])
        point = {
            "job_id": profile.get("job_id"),
            "parent_job_id": profile.get("parent_job_id"),
            "root_job_id": profile.get("root_job_id"),
            "change_id": profile.get("change_id"),
            "run_kind": profile.get("run_kind"),
            "status": profile.get("status"),
            "submitted_at": profile.get("submitted_at"),
            "complete_elapsed_seconds": profile.get("complete_elapsed_seconds"),
            "queue_seconds": profile.get("queue_seconds"),
            "runtime_seconds": profile.get("runtime_seconds"),
            "user_blocked_seconds": profile.get("user_blocked_seconds"),
            "tokens_total": tokens.get("tokens_total"),
            "estimated_cost_usd": tokens.get("estimated_cost_usd"),
            "runner": profile.get("runner"),
            "primary_model": profile.get("primary_model"),
            "models_used": profile.get("models_used") or [],
            "model_set": profile.get("model_set"),
            "original_ac_count": profile.get("original_ac_count"),
            "normalized_ac_count": profile.get("normalized_ac_count"),
            "loop_iterations": loop_iterations,
            "dominant_stage_by_time": profile.get("dominant_stage_by_time"),
            "dominant_stage_by_tokens": profile.get("dominant_stage_by_tokens"),
            "failed_stage": profile.get("failed_stage"),
        }
        duration_points.append(point)
        ac_points.append(point.copy())

    duration_series = []
    token_series = []
    stage_duration_heatmap = []
    stage_duration_boxplot = []
    stage_token_heatmap = []
    stage_token_boxplot = []
    loop_iteration_series = []
    stage_box_durations: dict[str, list[float]] = defaultdict(list)
    stage_box_failures: Counter[str] = Counter()
    stage_box_active: Counter[str] = Counter()
    stage_box_tokens: dict[str, list[float]] = defaultdict(list)
    for profile in profiles:
        for span in profile.get("stage_spans") or []:
            stage = str(span.get("stage") or "unattributed")
            if span.get("duration_seconds") is not None:
                stage_box_durations[stage].append(float(span.get("duration_seconds") or 0.0))
            if span.get("status") == "error":
                stage_box_failures[stage] += 1
            if span.get("active"):
                stage_box_active[stage] += 1
        for row in profile.get("token_by_stage") or []:
            stage = str(row.get("stage") or "unattributed")
            tokens_total = int(row.get("tokens_total") or 0)
            if tokens_total > 0:
                stage_box_tokens[stage].append(float(tokens_total))
    for key in sorted(grouped):
        bucket_profiles = grouped[key]
        elapsed = [float(p["complete_elapsed_seconds"]) for p in bucket_profiles if p.get("complete_elapsed_seconds") is not None]
        queue = [float(p["queue_seconds"]) for p in bucket_profiles if p.get("queue_seconds") is not None]
        runtime = [float(p["runtime_seconds"]) for p in bucket_profiles if p.get("runtime_seconds") is not None]
        blocked = [float(p["user_blocked_seconds"]) for p in bucket_profiles if p.get("user_blocked_seconds") is not None]
        total = len(bucket_profiles) or 1
        succeeded = sum(1 for p in bucket_profiles if p.get("status") == "succeeded")
        failed = sum(1 for p in bucket_profiles if p.get("status") == "failed")
        tokens_in = sum(int((p.get("tokens") or {}).get("tokens_in") or 0) for p in bucket_profiles)
        tokens_out = sum(int((p.get("tokens") or {}).get("tokens_out") or 0) for p in bucket_profiles)
        ac_total = sum(int(p.get("original_ac_count") or 0) for p in bucket_profiles if p.get("original_ac_count") is not None)
        loop_iterations = [
            sum(int(loop.get("actual_iterations") or 0) for loop in p.get("loop_instances") or [])
            for p in bucket_profiles
        ]
        loop_iterations_by_name: dict[str, list[int]] = defaultdict(list)
        for profile in bucket_profiles:
            per_run: Counter[str] = Counter()
            for loop in profile.get("loop_instances") or []:
                loop_name = str(loop.get("loop_name") or "unknown")
                per_run[loop_name] += int(loop.get("actual_iterations") or 0)
            for loop_name, iterations in per_run.items():
                loop_iterations_by_name[loop_name].append(int(iterations or 0))
        duration_series.append({
            "bucket": key,
            "run_count": len(bucket_profiles),
            "complete_elapsed_median_seconds": percentile(elapsed, 50),
            "complete_elapsed_p95_seconds": percentile(elapsed, 95),
            "queue_median_seconds": percentile(queue, 50),
            "runtime_median_seconds": percentile(runtime, 50),
            "user_blocked_median_seconds": percentile(blocked, 50),
            "success_rate": round(succeeded / total, 4),
            "failure_rate": round(failed / total, 4),
        })
        token_series.append({
            "bucket": key,
            "run_count": len(bucket_profiles),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out,
            "tokens_per_run": round((tokens_in + tokens_out) / total, 3),
            "tokens_per_success": round((tokens_in + tokens_out) / succeeded, 3) if succeeded else None,
            "tokens_per_original_ac": round((tokens_in + tokens_out) / ac_total, 3) if ac_total else None,
            "estimated_cost_usd": round(sum(float((p.get("tokens") or {}).get("estimated_cost_usd") or 0.0) for p in bucket_profiles), 6),
            "token_source": _token_source_for_profiles(bucket_profiles),
        })
        if loop_iterations_by_name:
            for loop_name, values in sorted(loop_iterations_by_name.items()):
                loop_iteration_series.append({
                    "bucket": key,
                    "loop_name": loop_name,
                    "run_count": len(values),
                    "median_iterations": percentile(values, 50),
                    "p95_iterations": percentile(values, 95),
                    "total_iterations": sum(values),
                })
        else:
            loop_iteration_series.append({
                "bucket": key,
                "loop_name": "no loop events",
                "run_count": len(bucket_profiles),
                "median_iterations": percentile(loop_iterations, 50),
                "p95_iterations": percentile(loop_iterations, 95),
                "total_iterations": sum(loop_iterations),
            })
        stage_durations: dict[str, list[float]] = defaultdict(list)
        stage_failures: Counter[str] = Counter()
        stage_active: Counter[str] = Counter()
        stage_tokens: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens_in": 0, "tokens_out": 0, "tokens_total": 0, "estimated_cost_usd": 0.0})
        for profile in bucket_profiles:
            for span in profile.get("stage_spans") or []:
                stage = str(span.get("stage") or "unattributed")
                if span.get("duration_seconds") is not None:
                    stage_durations[stage].append(float(span.get("duration_seconds") or 0.0))
                if span.get("status") == "error":
                    stage_failures[stage] += 1
                if span.get("active"):
                    stage_active[stage] += 1
            for row in profile.get("token_by_stage") or []:
                stage = str(row.get("stage") or "unattributed")
                item = stage_tokens[stage]
                item["tokens_in"] += int(row.get("tokens_in") or 0)
                item["tokens_out"] += int(row.get("tokens_out") or 0)
                item["tokens_total"] += int(row.get("tokens_total") or 0)
                item["estimated_cost_usd"] = round(float(item["estimated_cost_usd"]) + float(row.get("estimated_cost_usd") or 0.0), 6)
        for stage, values in sorted(stage_durations.items()):
            stage_duration_heatmap.append({
                "bucket": key,
                "stage": stage,
                "runs_observed": len(values),
                "median_seconds": percentile(values, 50),
                "p95_seconds": percentile(values, 95),
                "total_seconds": round(sum(values), 3),
                "failure_count": stage_failures[stage],
                "active_count": stage_active[stage],
            })
        for stage, values in sorted(stage_tokens.items()):
            stage_token_heatmap.append({"bucket": key, "stage": stage, **values, "token_source": "mixed"})

    def _stage_box_sort(item: tuple[str, list[float]]) -> tuple[float, float, float, str]:
        stage, values = item
        return (
            float(percentile(values, 95) or 0.0),
            float(percentile(values, 50) or 0.0),
            float(sum(values) if values else 0.0),
            stage,
        )

    for stage, values in sorted(stage_box_durations.items(), key=_stage_box_sort, reverse=True):
        failure_count = stage_box_failures[stage]
        observed = len(values)
        stage_duration_boxplot.append({
            "stage": stage,
            "runs_observed": observed,
            "failure_count": failure_count,
            "failure_rate": round(failure_count / observed, 4) if observed else 0,
            "active_count": stage_box_active[stage],
            "total_seconds": round(sum(values), 3),
            **distribution_summary(values),
        })

    for stage, values in sorted(stage_box_tokens.items(), key=_stage_box_sort, reverse=True):
        summary = distribution_summary(values)
        stage_token_boxplot.append({
            "stage": stage,
            "runs_observed": len(values),
            "total_tokens": int(sum(values)),
            "min_tokens": summary["min_seconds"],
            "q1_tokens": summary["q1_seconds"],
            "median_tokens": summary["median_seconds"],
            "q3_tokens": summary["q3_seconds"],
            "max_tokens": summary["max_seconds"],
            "p95_tokens": summary["p95_seconds"],
            "p99_tokens": summary["p99_seconds"],
            "mean_tokens": summary["mean_seconds"],
        })

    model_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    model_run_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parent_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        runner = str(profile.get("runner") or "unknown runner")
        model_names = [str(model) for model in (profile.get("models_used") or []) if model]
        if not model_names and profile.get("primary_model"):
            model_names = [str(profile.get("primary_model"))]
        model_names = sorted(set(model_names)) or ["unknown model"]
        token_rows_by_model = {
            str(row.get("model") or "unknown model"): row
            for row in (profile.get("token_by_model") or [])
        }
        for model_name in model_names:
            token_row = token_rows_by_model.get(model_name) or {}
            model_tokens = int(token_row.get("tokens_total") or 0)
            model_cost = float(token_row.get("estimated_cost_usd") or 0.0)
            if not token_rows_by_model and len(model_names) == 1:
                model_tokens = int((profile.get("tokens") or {}).get("tokens_total") or 0)
                model_cost = float((profile.get("tokens") or {}).get("estimated_cost_usd") or 0.0)
            item = {
                "profile": profile,
                "model": model_name,
                "tokens_total": model_tokens,
                "estimated_cost_usd": model_cost,
            }
            model_groups[(runner, model_name)].append(item)
            model_run_groups[model_name].append(item)
        parent_groups[str(profile.get("root_job_id") or profile.get("job_id") or "unknown")].append(profile)

    model_points = []
    for (runner, model_key), group in sorted(model_groups.items()):
        group_profiles = [item["profile"] for item in group]
        runtimes = [float(p.get("runtime_seconds")) for p in group_profiles if p.get("runtime_seconds") is not None]
        tokens_total = sum(int(item.get("tokens_total") or 0) for item in group)
        cost_total = sum(float(item.get("estimated_cost_usd") or 0.0) for item in group)
        successes = sum(1 for p in group_profiles if p.get("status") == "succeeded")
        failures = sum(1 for p in group_profiles if p.get("status") == "failed")
        total = len(group) or 1
        label = f"{runner} / {model_key}"
        model_points.append({
            "runner": runner,
            "model": model_key,
            "model_key": model_key,
            "runner_model": label,
            "model_set": label,
            "label": label,
            "runs": len(group),
            "success_rate": round(successes / total, 4),
            "failure_rate": round(failures / total, 4),
            "median_runtime_seconds": percentile(runtimes, 50),
            "p95_runtime_seconds": percentile(runtimes, 95),
            "tokens_per_run": round(tokens_total / total, 3),
            "cost_per_run": round(cost_total / total, 6),
            "cost_per_successful_run": round(cost_total / successes, 6) if successes else None,
            "most_common_failed_stage": Counter(p.get("failed_stage") for p in group_profiles if p.get("failed_stage")).most_common(1)[0][0]
            if any(p.get("failed_stage") for p in group_profiles) else None,
        })

    model_run_counts = []
    for model_name, group in sorted(model_run_groups.items()):
        group_profiles = [item["profile"] for item in group]
        runtimes = [float(p.get("runtime_seconds")) for p in group_profiles if p.get("runtime_seconds") is not None]
        tokens_total = sum(int(item.get("tokens_total") or 0) for item in group)
        successes = sum(1 for p in group_profiles if p.get("status") == "succeeded")
        failures = sum(1 for p in group_profiles if p.get("status") == "failed")
        total = len(group) or 1
        model_run_counts.append({
            "model": model_name,
            "model_key": model_name,
            "runs": len(group),
            "success_count": successes,
            "failure_count": failures,
            "success_rate": round(successes / total, 4),
            "failure_rate": round(failures / total, 4),
            "tokens_per_run": round(tokens_total / total, 3),
            "median_runtime_seconds": percentile(runtimes, 50),
            "runners": sorted({str(p.get("runner") or "unknown runner") for p in group_profiles}),
        })
    model_run_counts.sort(key=lambda row: (-int(row.get("runs") or 0), str(row.get("model") or "")))

    parent_rollups = []
    for root_id, group in sorted(parent_groups.items()):
        tokens_total = sum(int((p.get("tokens") or {}).get("tokens_total") or 0) for p in group)
        original_counts = [int(p.get("original_ac_count")) for p in group if p.get("original_ac_count") is not None]
        parent_rollups.append({
            "root_job_id": root_id,
            "change_id": group[0].get("change_id"),
            "run_kind": group[0].get("run_kind"),
            "child_count": max(0, len(group) - 1),
            "complete_elapsed_seconds": max((float(p.get("complete_elapsed_seconds") or 0.0) for p in group), default=0.0),
            "child_runtime_sum_seconds": round(sum(float(p.get("runtime_seconds") or 0.0) for p in group), 3),
            "tokens_total": tokens_total,
            "estimated_cost_usd": round(sum(float((p.get("tokens") or {}).get("estimated_cost_usd") or 0.0) for p in group), 6),
            "success_count": sum(1 for p in group if p.get("status") == "succeeded"),
            "failure_count": sum(1 for p in group if p.get("status") == "failed"),
            "models_used": sorted({model for p in group for model in (p.get("models_used") or [])}),
            "original_ac_count_total": sum(original_counts) if original_counts else None,
            "original_ac_count_median": percentile([float(value) for value in original_counts], 50),
        })

    return {
        "bucket": bucket,
        "bucket_tz": "UTC",
        "rollup": rollup,
        "duration_points": duration_points,
        "duration_series": duration_series,
        "token_series": token_series,
        "stage_duration_heatmap": stage_duration_heatmap,
        "stage_duration_boxplot": stage_duration_boxplot,
        "stage_token_heatmap": stage_token_heatmap,
        "stage_token_boxplot": stage_token_boxplot,
        "model_points": model_points,
        "model_run_counts": model_run_counts,
        "ac_complexity_points": ac_points,
        "loop_iteration_series": loop_iteration_series,
        "parent_rollups": parent_rollups,
        "coverage": {
            "profile_count": len(profiles),
            "with_events": sum(1 for p in profiles if (p.get("coverage") or {}).get("has_events")),
            "with_llm_calls": sum(1 for p in profiles if (p.get("coverage") or {}).get("has_llm_calls")),
            "with_stage_spans": sum(1 for p in profiles if (p.get("coverage") or {}).get("has_stage_spans")),
            "with_original_ac_count": sum(1 for p in profiles if (p.get("coverage") or {}).get("has_original_ac_count")),
            "with_loop_events": sum(1 for p in profiles if (p.get("coverage") or {}).get("has_loop_events")),
        },
    }
