"""Subprocess management for agent-runner jobs.

Spawns `python run.py` with event-log + cassette env vars,
manages cancel/kill, and tracks PID in the database.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

from server.config import load_config
from server.db import update_job, get_job
from datetime import datetime, timezone

_RUNNER_ROOT = Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def start_job_process(job: dict[str, Any]) -> asyncio.subprocess.Process:
    """Spawn run.py as an asyncio subprocess for the given job dict."""
    cfg = load_config()
    runner_root = cfg["paths"]["runner_root"] or str(_RUNNER_ROOT)
    agent_context = cfg["paths"]["agent_context"] or str(_RUNNER_ROOT / "agent-context")

    change_id = job["change_id"]
    events_path = Path(agent_context) / change_id / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["AGENT_RUNNER_EVENT_LOG"] = str(events_path)

    if job.get("mode") == "hermetic":
        cassettes_dir = Path(cfg["paths"]["data_dir"]).expanduser() / "cassettes"
        cassettes_dir.mkdir(parents=True, exist_ok=True)
        cassette_path = cassettes_dir / f"{change_id}.jsonl"
        env["AGENT_RUNNER_CASSETTE"] = str(cassette_path)
    else:
        cassette_path = None

    cmd = [sys.executable, str(Path(runner_root) / "run.py")]
    if job.get("repo"):
        cmd += ["--repo", job["repo"]]
    if job.get("change_id"):
        cmd += ["--change-id", job["change_id"]]
    if job.get("ado_url"):
        cmd += ["--ado-url", job["ado_url"]]
    if job.get("story_file"):
        cmd += ["--story-file", job["story_file"]]
    if job.get("runner"):
        cmd += ["--runner", job["runner"]]
    if job.get("model"):
        cmd += ["--model", job["model"]]
    if job.get("copilot_effort"):
        cmd += ["--copilot-effort", job["copilot_effort"]]
    if job.get("skip_materialize"):
        cmd.append("--skip-materialize")
    if job.get("extra_context"):
        cmd += ["--extra-context", job["extra_context"]]

    updates: dict[str, Any] = {
        "events_path": str(events_path),
        "started_at": _now_iso(),
        "status": "running",
    }
    if cassette_path:
        updates["cassette_path"] = str(cassette_path)
    update_job(job["id"], updates)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=runner_root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    update_job(job["id"], {"pid": proc.pid})
    return proc


async def cancel_job(job_id: str) -> bool:
    """Send SIGTERM to the running job. Returns True if signal was sent."""
    job = get_job(job_id)
    if not job:
        return False
    if job["status"] != "running":
        return False
    pid = job.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        update_job(job_id, {
            "status": "cancelled",
            "finished_at": _now_iso(),
        })
        return True
    except (ProcessLookupError, PermissionError):
        return False
