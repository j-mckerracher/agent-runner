"""Subprocess management: spawn run.py per job, manage cancel and lifecycle."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from . import db
from .events import EventBus, FileTailer, aggregate, read_all
from .paths import AGENT_CONTEXT_ROOT, RUNNER_ROOT, cassettes_dir, events_path_for

logger = logging.getLogger(__name__)


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _format_failure_summary(events: list[dict], output_text: str, exit_code: int) -> str:
    preferred_kinds = ("command_failed", "validation_failed", "stage_failed", "workflow_failed")
    failed_stage = next(
        (
            evt.get("stage")
            for evt in reversed(events)
            if evt.get("type") == "stage.end" and evt.get("status") == "error" and evt.get("stage")
        ),
        None,
    )

    selected_event: dict[str, Any] | None = None
    for kind in preferred_kinds:
        selected_event = next(
            (
                evt for evt in reversed(events)
                if evt.get("type") == "log"
                and evt.get("level") == "error"
                and evt.get("kind") == kind
                and evt.get("msg")
            ),
            None,
        )
        if selected_event is not None:
            break
    if selected_event is None:
        selected_event = next(
            (
                evt for evt in reversed(events)
                if evt.get("type") == "log" and evt.get("level") == "error" and evt.get("msg")
            ),
            None,
        )

    if selected_event is not None:
        msg = str(selected_event.get("msg") or "").strip()
        stage = selected_event.get("stage") or failed_stage
        if stage and not msg.lower().startswith(f"{stage.lower()}:"):
            msg = f"{stage}: {msg}"
        return msg[:1000]

    output_line = _last_nonempty_line(output_text)
    if output_line:
        if failed_stage and not output_line.lower().startswith(f"{failed_stage.lower()}:"):
            output_line = f"{failed_stage}: {output_line}"
        return output_line[:1000]

    if failed_stage:
        return f"{failed_stage} failed (exit {exit_code})"
    return f"Run failed with exit code {exit_code}"


class JobProcess:
    """One run.py subprocess per job."""

    def __init__(self, job: dict, bus: EventBus, loop: asyncio.AbstractEventLoop) -> None:
        self.job = job
        self.bus = bus
        self.loop = loop
        self.proc: subprocess.Popen | None = None
        self.tailer: FileTailer | None = None
        self._lock = threading.Lock()
        logger.debug("JobProcess: created for job_id=%s change_id=%s", job.get("id"), job.get("change_id"))

    @property
    def id(self) -> str:
        return self.job["id"]

    def _build_cmd(self) -> list[str]:
        py = sys.executable or "python3"
        cmd = [py, str(RUNNER_ROOT / "run.py")]
        cmd += ["--repo", self.job["repo"]]
        cmd += ["--change-id", self.job["change_id"]]
        if self.job.get("ado_url"):
            cmd += ["--ado-url", self.job["ado_url"]]
        elif self.job.get("story_file"):
            cmd += ["--story-file", self.job["story_file"]]
        cmd += ["--runner", self.job["runner"]]
        if self.job.get("model"):
            cmd += ["--model", self.job["model"]]
        if self.job.get("copilot_effort"):
            cmd += ["--copilot-effort", self.job["copilot_effort"]]
        if self.job.get("skip_materialize"):
            cmd += ["--skip-materialize"]
        if self.job.get("extra_context"):
            cmd += ["--extra-context", self.job["extra_context"]]
        logger.debug("JobProcess._build_cmd: job_id=%s cmd=%s", self.id, cmd)
        return cmd

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["AGENT_RUNNER_EVENT_LOG"] = self.job["events_path"]
        env["AGENT_RUNNER_JOB_ID"] = self.id
        env["AGENT_CONTEXT_ROOT"] = str(AGENT_CONTEXT_ROOT)
        env["CHANGE_ID"] = self.job["change_id"]
        if self.job.get("cassette_path"):
            env["AGENT_RUNNER_CASSETTE"] = self.job["cassette_path"]
        logger.debug("JobProcess._build_env: job_id=%s event_log=%s cassette=%s", self.id, self.job["events_path"], self.job.get("cassette_path"))
        return env

    def start(self) -> None:
        cmd = self._build_cmd()
        env = self._build_env()
        logger.info("JobProcess.start: job_id=%s runner=%s change_id=%s", self.id, self.job.get("runner"), self.job.get("change_id"))
        # Truncate any pre-existing event log so seq starts fresh.
        Path(self.job["events_path"]).write_text("", encoding="utf-8")
        logger.debug("JobProcess.start: truncated event log %s", self.job["events_path"])

        # Tailer must be running before subprocess so we capture all events.
        def _persist(evt: dict) -> None:
            t = evt.get("type")
            if t == "stage.start":
                logger.debug("JobProcess._persist: job_id=%s stage.start stage=%s", self.id, evt.get("stage"))
                db.update_job(self.id, current_stage=evt.get("stage"))
            elif t == "user.prompt":
                logger.info("JobProcess._persist: job_id=%s awaiting user input", self.id)
                db.update_job(self.id, status="awaiting_input")
            elif t in ("user.response", "user.prompt.timeout"):
                logger.info("JobProcess._persist: job_id=%s resuming after user.%s", self.id, t.split(".")[-1])
                db.update_job(self.id, status="running")
            elif t == "metrics":
                row = db.get_job(self.id) or {}
                ti = int(row.get("tokens_in") or 0) + int(evt.get("tokens_in") or 0)
                to = int(row.get("tokens_out") or 0) + int(evt.get("tokens_out") or 0)
                cu = float(row.get("cost_usd") or 0.0) + float(evt.get("cost_usd") or 0.0)
                logger.debug("JobProcess._persist: job_id=%s metrics tokens_in=%d tokens_out=%d cost_usd=%f", self.id, ti, to, cu)
                db.update_job(self.id, tokens_in=ti, tokens_out=to, cost_usd=cu)

        self.tailer = FileTailer(self.job["events_path"], self.id, self.bus, self.loop, on_event=_persist)
        self.tailer.start()
        logger.debug("JobProcess.start: FileTailer started for job_id=%s", self.id)

        # Spawn in its own process group so we can SIGTERM the whole tree on cancel.
        kwargs: dict[str, Any] = dict(
            cwd=str(RUNNER_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if os.name != "nt":
            kwargs["preexec_fn"] = os.setsid
        self.proc = subprocess.Popen(cmd, **kwargs)
        logger.info("JobProcess.start: subprocess spawned job_id=%s pid=%d", self.id, self.proc.pid)
        db.update_job(self.id, pid=self.proc.pid, status="running", started_at=db.now_iso())

    def cancel(self) -> bool:
        with self._lock:
            if self.proc is None:
                logger.warning("JobProcess.cancel: job_id=%s no process to cancel", self.id)
                return False
            if self.proc.poll() is not None:
                logger.info("JobProcess.cancel: job_id=%s process already exited (rc=%s)", self.id, self.proc.returncode)
                return False
            try:
                if os.name == "nt":
                    self.proc.terminate()
                    logger.info("JobProcess.cancel: job_id=%s SIGTERM sent (Windows terminate)", self.id)
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    logger.info("JobProcess.cancel: job_id=%s SIGTERM sent to process group (pid=%d)", self.id, self.proc.pid)
                return True
            except (ProcessLookupError, OSError) as exc:
                logger.warning("JobProcess.cancel: job_id=%s failed to send signal: %s", self.id, exc)
                return False

    async def wait(self) -> int:
        assert self.proc is not None
        logger.debug("JobProcess.wait: waiting for job_id=%s (pid=%d)", self.id, self.proc.pid)

        loop = asyncio.get_running_loop()

        def _read_stdout() -> str:
            assert self.proc is not None
            if self.proc.stdout is None:
                return ""
            try:
                return self.proc.stdout.read() or ""
            except Exception as exc:
                logger.warning("JobProcess._read_stdout: job_id=%s error reading stdout: %s", self.id, exc)
                return ""

        def _wait() -> int:
            assert self.proc is not None
            return self.proc.wait()

        # Drain stdout in background so PIPE doesn't fill (we already capture
        # structured events from the JSONL file; stdout is just informational).
        drain = loop.run_in_executor(None, _read_stdout)
        rc = await loop.run_in_executor(None, _wait)
        logger.info("JobProcess.wait: job_id=%s subprocess exited (rc=%d)", self.id, rc)
        stdout_text = ""
        try:
            stdout_text = await drain
        except Exception as exc:
            logger.warning("JobProcess.wait: job_id=%s error draining stdout: %s", self.id, exc)
            stdout_text = ""

        if stdout_text:
            logger.debug("JobProcess.wait: job_id=%s stdout tail: %s", self.id, stdout_text[-500:])

        # Wait briefly for tailer to flush trailing events.
        if self.tailer is not None:
            self.tailer.stop()
            self.tailer.join(timeout=2.0)
            if self.tailer.is_alive():
                logger.warning("JobProcess.wait: job_id=%s FileTailer did not exit within 2s", self.id)
            else:
                logger.debug("JobProcess.wait: job_id=%s FileTailer exited cleanly", self.id)

        # Determine final status. Prefer explicit job.end event.
        events = read_all(self.job["events_path"])
        agg = aggregate(events)
        logger.debug("JobProcess.wait: job_id=%s aggregate=%s", self.id, agg)
        if agg["final_status"] == "cancelled":
            status = "cancelled"
        elif rc == 0:
            status = "succeeded"
        elif rc < 0 and -rc in (signal.SIGTERM, signal.SIGINT):
            status = "cancelled"
        else:
            status = "failed"

        error_message = None
        if status == "failed":
            error_message = _format_failure_summary(events, stdout_text, rc)
            logger.warning("JobProcess.wait: job_id=%s FAILED: %s", self.id, error_message)
        else:
            logger.info("JobProcess.wait: job_id=%s final status=%s", self.id, status)

        db.update_job(
            self.id,
            status=status,
            exit_code=rc,
            finished_at=db.now_iso(),
            current_stage=None,
            error_message=error_message,
        )
        return rc

# Pre-flight helpers used by the route layer.
# ─────────────────────────────────────────────────────────────────────────────


def prepare_job_paths(change_id: str, mode: str) -> tuple[str, str | None]:
    """Return (events_path, cassette_path_or_none) for a new job."""
    logger.debug("prepare_job_paths: change_id=%s mode=%s", change_id, mode)
    events = str(events_path_for(change_id))
    cassette: str | None = None
    if mode == "hermetic":
        cassette = str(cassettes_dir() / f"{change_id}.jsonl")
        logger.debug("prepare_job_paths: hermetic mode; cassette=%s", cassette)
    logger.debug("prepare_job_paths: events=%s cassette=%s", events, cassette)
    return events, cassette
