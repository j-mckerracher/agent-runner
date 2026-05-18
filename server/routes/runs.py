"""Runs API: submit, list, detail, events, SSE stream, cancel."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any, Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from core.cli_logging import normalize_log_level
from core.workflow_inputs import resolve_workflow_input

from .. import db
from ..config import load_config
from ..events import read_all
from ..jobs import manager
from ..paths import user_responses_path_for
from core.runner_models import KNOWN_RUNNERS, resolve_runner_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _clean_opik_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _build_opik_dashboard_url(change_id: str, opik_cfg: dict[str, Any]) -> str | None:
    dashboard_url = _clean_opik_value(opik_cfg.get("dashboard_url")).rstrip("/")
    workspace_name = _clean_opik_value(opik_cfg.get("workspace_name"))
    project_id = _clean_opik_value(opik_cfg.get("project_id"))
    thread_id = change_id.strip()
    if not dashboard_url or not workspace_name or not project_id or not thread_id:
        return None
    filters = json.dumps(
        [{"field": "thread_id", "type": "string", "operator": "=", "value": thread_id}],
        separators=(",", ":"),
    )
    query = urlencode(
        {
            "tab": "logs",
            "logsType": "traces",
            "traces_filters": filters,
        }
    )
    path = f"/workspaceGuard/{quote(workspace_name, safe='')}/projects/{quote(project_id, safe='')}"
    return f"{dashboard_url}{path}?{query}"


def _build_opik_context(row: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config().get("opik") or {}
    change_id = _clean_opik_value(row.get("change_id"))
    return {
        "project_name": _clean_opik_value(cfg.get("project_name")),
        "workspace_name": _clean_opik_value(cfg.get("workspace_name")),
        "project_id": _clean_opik_value(cfg.get("project_id")),
        "thread_id": change_id,
        "dashboard_url": _build_opik_dashboard_url(change_id, cfg),
    }


class RunSubmit(BaseModel):
    repo: str
    change_id: str
    runner: str = "claude"
    model: Optional[str] = None
    log_level: str = "warning"
    mode: str = Field("live", pattern="^(live|hermetic)$")
    run_kind: Optional[str] = None
    ado_url: Optional[str] = None
    story_file: Optional[str] = None
    extra_context: Optional[str] = None
    parent_job_id: Optional[str] = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        if value is None:
            return "warning"
        try:
            return normalize_log_level(str(value))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(str(exc)) from exc


@router.post("")
async def submit_run(payload: RunSubmit) -> dict[str, Any]:
    logger.info("submit_run: change_id=%s runner=%s mode=%s", payload.change_id, payload.runner, payload.mode)
    cfg = load_config()
    valid_runners = set(KNOWN_RUNNERS) | set((cfg.get("runner_aliases") or {}).keys())
    if payload.runner not in valid_runners:
        logger.warning("submit_run: invalid runner=%s", payload.runner)
        raise HTTPException(
            400,
            f"runner must be one of: {', '.join(sorted(valid_runners))}"
        )
    if payload.run_kind and payload.run_kind != "regular":
        logger.warning("submit_run: invalid run_kind=%s", payload.run_kind)
        raise HTTPException(400, "regular runs must be submitted through /runs")
    if payload.ado_url and payload.story_file:
        logger.warning("submit_run: both ado_url and story_file provided")
        raise HTTPException(400, "provide ado_url OR story_file, not both")
    try:
        resolve_workflow_input(
            repo=payload.repo,
            change_id=payload.change_id,
            ado_url=payload.ado_url,
            story_file=payload.story_file,
        )
        logger.debug("submit_run: workflow input resolved successfully for change_id=%s", payload.change_id)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("submit_run: resolve_workflow_input failed: %s", exc)
        raise HTTPException(400, str(exc)) from exc
    submit_payload = payload.model_dump()
    submit_payload["run_kind"] = "regular"
    job_id = await manager().submit(submit_payload)
    logger.info("submit_run: job submitted job_id=%s change_id=%s", job_id, payload.change_id)
    return {"job_id": job_id}


@router.get("")
async def list_runs(
    status: Optional[str] = None,
    change_id: Optional[str] = None,
    run_kind: str = Query("regular", pattern="^(regular|evaluation|all)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    logger.debug(
        "list_runs: status=%s change_id=%s run_kind=%s limit=%d offset=%d",
        status, change_id, run_kind, limit, offset,
    )
    effective_run_kind = None if run_kind == "all" else run_kind
    rows = db.list_jobs(status=status, change_id=change_id, run_kind=effective_run_kind, limit=limit, offset=offset)
    logger.debug("list_runs: returning %d row(s)", len(rows))
    return {"items": rows, "count": len(rows)}


@router.get("/{job_id}")
async def get_run(job_id: str) -> dict[str, Any]:
    logger.debug("get_run: job_id=%s", job_id)
    row = db.get_job(job_id)
    if not row:
        logger.warning("get_run: job_id=%s not found", job_id)
        raise HTTPException(404, "job not found")
    row["children"] = db.list_children(job_id)
    row["opik"] = _build_opik_context(row)
    logger.debug("get_run: job_id=%s status=%s children=%d", job_id, row.get("status"), len(row["children"]))
    return row


@router.get("/{job_id}/events")
async def get_run_events(job_id: str) -> list[dict[str, Any]]:
    logger.debug("get_run_events: job_id=%s", job_id)
    row = db.get_job(job_id)
    if not row:
        logger.warning("get_run_events: job_id=%s not found", job_id)
        raise HTTPException(404, "job not found")
    events = read_all(row["events_path"]) if row.get("events_path") else []
    logger.debug("get_run_events: job_id=%s returning %d event(s)", job_id, len(events))
    return events


class UserResponseBody(BaseModel):
    conversation_id: str | None = None
    escalation_id: str | None = None
    message: str | None = None
    responses: dict[str, str] = Field(default_factory=dict)


@router.post("/{job_id}/respond")
async def respond_to_run(job_id: str, body: UserResponseBody) -> dict[str, Any]:
    logger.info("respond_to_run: job_id=%s conversation_id=%s escalation_id=%s", job_id, body.conversation_id, body.escalation_id)
    job = db.get_job(job_id)
    if not job:
        logger.warning("respond_to_run: job_id=%s not found", job_id)
        raise HTTPException(404, "job not found")
    terminal_statuses = ("succeeded", "failed", "cancelled")
    if job["status"] in terminal_statuses:
        logger.warning("respond_to_run: job_id=%s is terminal (%s)", job_id, job["status"])
        raise HTTPException(409, f"job is terminal ({job['status']})")
    if job["status"] != "awaiting_input":
        logger.warning("respond_to_run: job_id=%s status=%s not awaiting_input", job_id, job["status"])
        raise HTTPException(409, "job is not awaiting input")

    change_id = job["change_id"]

    # ── New escalation flow (conversation_id + escalation_id present) ──
    if body.escalation_id:
        from core.user_escalation import write_user_response
        from server.events import append_event

        conv_id = body.conversation_id or ""
        if not conv_id:
            raise HTTPException(422, "conversation_id is required when escalation_id is provided")
        try:
            response_record = write_user_response(
                change_id=change_id,
                job_id=job_id,
                conversation_id=conv_id,
                escalation_id=body.escalation_id,
                message=body.message,
                responses=body.responses,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc))

        # Emit user.response to the event log (cross-process safe).
        events_path = job.get("events_path")
        if events_path:
            append_event(
                events_path,
                "user.response",
                change_id=change_id,
                job_id=job_id,
                conversation_id=conv_id,
                escalation_id=body.escalation_id,
                message=body.message,
                responses=body.responses,
                pending_count_after=response_record["pending_count_after"],
                responded_at=response_record["responded_at"],
            )

        pending = response_record["pending_count_after"]
        new_status = "awaiting_input" if pending > 0 else "running"
        db.update_job(job_id, status=new_status)
        logger.info(
            "respond_to_run: job_id=%s escalation response written conv=%s esc=%s pending=%d status=%s",
            job_id, conv_id, body.escalation_id, pending, new_status,
        )
        return {
            "ok": True,
            "conversation_id": conv_id,
            "escalation_id": body.escalation_id,
            "pending_count_after": pending,
        }

    # ── Legacy intake-only flow (no escalation_id) ──
    responses_path = user_responses_path_for(change_id)
    responses_path.parent.mkdir(parents=True, exist_ok=True)
    responses_path.write_text(json.dumps({"responses": body.responses}), encoding="utf-8")
    db.update_job(job_id, status="running")
    logger.info("respond_to_run: job_id=%s legacy responses written to %s", job_id, responses_path)
    return {"ok": True}


@router.post("/{job_id}/cancel")
async def cancel_run(job_id: str) -> dict[str, Any]:
    logger.info("cancel_run: job_id=%s", job_id)
    ok = await manager().cancel(job_id)
    if not ok:
        logger.warning("cancel_run: job_id=%s not cancellable", job_id)
        raise HTTPException(409, "job not cancellable")
    logger.info("cancel_run: job_id=%s cancelled", job_id)
    return {"cancelled": True}


@router.get("/{job_id}/stream")
async def stream_run(
    request: Request,
    job_id: str,
    after: int = Query(0, ge=0),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    logger.info("stream_run: job_id=%s after=%d last_event_id=%s", job_id, after, last_event_id)
    row = db.get_job(job_id)
    if not row:
        logger.warning("stream_run: job_id=%s not found", job_id)
        raise HTTPException(404, "job not found")
    bus = request.app.state.bus
    queue = bus.subscribe(job_id)

    after_seq = after
    if last_event_id:
        try:
            after_seq = max(after_seq, int(last_event_id))
            logger.debug("stream_run: job_id=%s after_seq adjusted to %d via Last-Event-ID", job_id, after_seq)
        except ValueError:
            logger.debug("stream_run: job_id=%s non-integer Last-Event-ID=%s ignored", job_id, last_event_id)

    async def gen():
        events_sent = 0
        try:
            for evt in read_all(row["events_path"]):
                seq = int(evt.get("seq", 0))
                if seq <= after_seq:
                    continue
                events_sent += 1
                yield _sse(evt)
                if evt.get("type") == "job.end":
                    logger.debug("stream_run gen: job_id=%s job.end found in history; closing", job_id)
                    yield _sse({"type": "stream.end"})
                    return
            logger.debug("stream_run gen: job_id=%s history replayed (%d event(s)); switching to live", job_id, events_sent)
            while True:
                if await request.is_disconnected():
                    logger.info("stream_run gen: job_id=%s client disconnected after %d event(s)", job_id, events_sent)
                    return
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.debug("stream_run gen: job_id=%s timeout — sending keepalive ping", job_id)
                    yield ": ping\n\n"
                    continue
                events_sent += 1
                yield _sse(evt)
                if evt.get("type") in ("job.end", "stream.end"):
                    logger.info("stream_run gen: job_id=%s stream ended after %d event(s)", job_id, events_sent)
                    yield _sse({"type": "stream.end"})
                    return
        finally:
            bus.unsubscribe(job_id, queue)
            logger.debug("stream_run gen: job_id=%s unsubscribed from bus", job_id)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(evt: dict) -> str:
    seq = evt.get("seq")
    head = f"id: {seq}\n" if seq else ""
    return f"{head}event: {evt.get('type', 'message')}\ndata: {json.dumps(evt)}\n\n"
