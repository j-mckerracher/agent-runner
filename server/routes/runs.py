"""Runs routes: submit, list, get, events, SSE stream, cancel."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from server.db import insert_job, get_job, list_jobs, update_job
from server.events import read_events_from_file, EventTailer
from server.runner_proc import start_job_process, cancel_job

router = APIRouter(prefix="/runs", tags=["runs"])

# In-memory map: job_id -> EventTailer
_tailers: dict[str, EventTailer] = {}
# In-memory map: job_id -> asyncio.Task
_tasks: dict[str, asyncio.Task] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:12]


class SubmitJobRequest(BaseModel):
    repo: str = "."
    change_id: str | None = None
    ado_url: str | None = None
    story_file: str | None = None
    runner: str = "claude"
    model: str | None = None
    copilot_effort: str | None = None
    skip_materialize: bool = False
    extra_context: str | None = None
    mode: str = "live"  # hermetic | live


@router.post("", status_code=201)
async def submit_run(body: SubmitJobRequest, request: Request) -> dict[str, Any]:
    job_id = _new_job_id()
    change_id = body.change_id or job_id
    job: dict[str, Any] = {
        "id": job_id,
        "change_id": change_id,
        "parent_job_id": None,
        "status": "queued",
        "mode": body.mode,
        "runner": body.runner,
        "model": body.model,
        "copilot_effort": body.copilot_effort,
        "repo": body.repo,
        "ado_url": body.ado_url,
        "story_file": body.story_file,
        "extra_context": body.extra_context,
        "skip_materialize": 1 if body.skip_materialize else 0,
        "submitted_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "error_message": None,
        "pid": None,
        "events_path": None,
        "cassette_path": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "current_stage": None,
    }
    insert_job(job)

    # Schedule the job via the queue processor (accessed via app state)
    app = request.app
    queue: asyncio.Queue = app.state.job_queue
    await queue.put(job_id)

    return {"job_id": job_id, "change_id": change_id, "status": "queued"}


@router.get("")
def list_runs(
    status: str | None = Query(default=None),
    change_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    return list_jobs(status=status, change_id=change_id, limit=limit, offset=offset)


@router.get("/{job_id}")
def get_run(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/events")
def get_run_events(job_id: str) -> list[dict[str, Any]]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    events_path = job.get("events_path")
    if not events_path:
        return []
    return read_events_from_file(events_path)


@router.get("/{job_id}/stream")
async def stream_run(job_id: str, request: Request) -> EventSourceResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Determine resume point from Last-Event-ID header
    last_event_id_raw = request.headers.get("last-event-id", "0")
    try:
        resume_seq = int(last_event_id_raw)
    except ValueError:
        resume_seq = 0

    async def event_generator():
        events_path = job.get("events_path")

        # Replay historical events first
        if events_path:
            past = read_events_from_file(events_path)
            for evt in past:
                seq = evt.get("seq", 0)
                if seq > resume_seq:
                    yield {
                        "id": str(seq),
                        "data": __import__("json").dumps(evt),
                    }

        # If job is terminal, no need to tail
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return

        # Get or create tailer
        tailer = _tailers.get(job_id)
        if tailer is None or tailer._stopped:
            if events_path:
                tailer = EventTailer(events_path)
                _tailers[job_id] = tailer
                task = asyncio.create_task(tailer.run())
                _tasks[job_id] = task
            else:
                return

        q = await tailer.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
                    continue
                if event is None:  # sentinel — stream ended
                    break
                seq = event.get("seq", 0)
                yield {
                    "id": str(seq),
                    "data": __import__("json").dumps(event),
                }
                if event.get("type") == "job.end":
                    break
        finally:
            await tailer.unsubscribe(q)

    return EventSourceResponse(event_generator())


@router.post("/{job_id}/cancel")
async def cancel_run(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, cannot cancel")
    if job["status"] == "queued":
        update_job(job_id, {"status": "cancelled", "finished_at": _now_iso()})
        return {"cancelled": True}
    ok = await cancel_job(job_id)
    return {"cancelled": ok}


# ── Queue processor ───────────────────────────────────────────────────────────

async def queue_processor(app) -> None:
    """Drains the job queue respecting concurrency limits."""
    from server.db import count_running_jobs
    from server.config import load_config

    queue: asyncio.Queue = app.state.job_queue

    while True:
        job_id = await queue.get()
        # Wait if at concurrency limit
        while True:
            cfg = load_config()
            max_jobs = cfg.get("concurrency", {}).get("max_running_jobs", 2)
            running = count_running_jobs()
            if running < max_jobs:
                break
            await asyncio.sleep(1)

        job = get_job(job_id)
        if not job or job["status"] != "queued":
            continue

        # Start the subprocess
        try:
            proc = await start_job_process(job)
        except Exception as exc:
            update_job(job_id, {
                "status": "failed",
                "finished_at": _now_iso(),
                "error_message": str(exc),
            })
            continue

        # Monitor in background
        asyncio.create_task(_monitor_job(job_id, proc))


async def _monitor_job(job_id: str, proc: asyncio.subprocess.Process) -> None:
    """Wait for process completion and update job status."""
    from server.events import read_events_from_file
    try:
        await proc.wait()
    except Exception:
        pass
    exit_code = proc.returncode or 0
    status = "succeeded" if exit_code == 0 else "failed"

    job = get_job(job_id)
    updates: dict[str, Any] = {
        "status": status,
        "finished_at": _now_iso(),
        "exit_code": exit_code,
    }

    # Aggregate token metrics from events
    if job and job.get("events_path"):
        events = read_events_from_file(job["events_path"])
        tokens_in = sum(e.get("tokens_in", 0) for e in events if e.get("type") == "metrics")
        tokens_out = sum(e.get("tokens_out", 0) for e in events if e.get("type") == "metrics")
        cost_usd = sum(e.get("cost_usd", 0.0) for e in events if e.get("type") == "metrics")
        updates["tokens_in"] = tokens_in
        updates["tokens_out"] = tokens_out
        updates["cost_usd"] = cost_usd

    # Stop tailer if running
    tailer = _tailers.get(job_id)
    if tailer:
        tailer.stop()

    update_job(job_id, updates)
