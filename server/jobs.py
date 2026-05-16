"""Job submission, queueing, lifecycle."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from . import db
from .config import load_config
from .events import EventBus
from .runner_proc import JobProcess, prepare_job_paths

logger = logging.getLogger(__name__)


class JobManager:
    """Owns the asyncio queue and process registry."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._procs: dict[str, JobProcess] = {}
        self._workers: list[asyncio.Task] = []
        self._started = False
        self._lock = asyncio.Lock()
        logger.debug("JobManager: initialised")

    async def start(self) -> None:
        if self._started:
            logger.debug("JobManager.start: already started; skipping")
            return
        self._started = True
        cfg = load_config()
        max_workers = int(cfg.get("concurrency", {}).get("max_running_jobs", 2))
        logger.info("JobManager.start: spawning %d worker task(s)", max(1, max_workers))
        for _ in range(max(1, max_workers)):
            self._workers.append(asyncio.create_task(self._worker()))
        logger.info("JobManager.start: %d worker(s) running", len(self._workers))

    async def shutdown(self) -> None:
        logger.info("JobManager.shutdown: cancelling %d worker(s) and %d active proc(s)", len(self._workers), len(self._procs))
        for t in self._workers:
            t.cancel()
        for job_id, jp in list(self._procs.items()):
            logger.debug("JobManager.shutdown: cancelling process for job_id=%s", job_id)
            try:
                jp.cancel()
            except Exception as exc:
                logger.warning("JobManager.shutdown: error cancelling job_id=%s: %s", job_id, exc)
        self._workers.clear()
        self._started = False
        logger.info("JobManager.shutdown: complete")

    async def submit(self, payload: dict[str, Any]) -> str:
        job_id = "job_" + secrets.token_hex(8)
        change_id = payload["change_id"]
        mode = payload.get("mode", "live")
        logger.info("JobManager.submit: job_id=%s change_id=%s runner=%s mode=%s", job_id, change_id, payload.get("runner"), mode)
        events_path, cassette_path = prepare_job_paths(change_id, mode)
        record = {
            "id": job_id,
            "change_id": change_id,
            "parent_job_id": payload.get("parent_job_id"),
            "status": "queued",
            "run_kind": payload.get("run_kind", "regular"),
            "mode": mode,
            "runner": payload["runner"],
            "model": payload.get("model"),
            "log_level": payload.get("log_level", "warning"),
            "repo": payload["repo"],
            "ado_url": payload.get("ado_url"),
            "story_file": payload.get("story_file"),
            "extra_context": payload.get("extra_context"),
            "submitted_at": db.now_iso(),
            "events_path": events_path,
            "cassette_path": cassette_path,
        }
        db.insert_job(record)
        await self._queue.put(job_id)
        logger.info("JobManager.submit: job_id=%s queued (queue_size≈%d)", job_id, self._queue.qsize())
        return job_id

    async def cancel(self, job_id: str) -> bool:
        logger.info("JobManager.cancel: requested for job_id=%s", job_id)
        job = db.get_job(job_id)
        if not job:
            logger.warning("JobManager.cancel: job_id=%s not found", job_id)
            return False
        if job["status"] in ("succeeded", "failed", "cancelled"):
            logger.info("JobManager.cancel: job_id=%s already terminal (status=%s)", job_id, job["status"])
            return False
        jp = self._procs.get(job_id)
        if jp is not None:
            ok = jp.cancel()
            if ok:
                logger.info("JobManager.cancel: job_id=%s process cancelled", job_id)
                db.update_job(job_id, status="cancelled")
            else:
                logger.warning("JobManager.cancel: job_id=%s process cancel returned False", job_id)
            return ok
        # Queued but not yet started: mark cancelled directly.
        logger.info("JobManager.cancel: job_id=%s was queued (not running); marking cancelled", job_id)
        db.update_job(job_id, status="cancelled", finished_at=db.now_iso())
        return True

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        logger.debug("JobManager._worker: worker started")
        while True:
            job_id = await self._queue.get()
            start_ts = time.monotonic()
            logger.info("JobManager._worker: dequeued job_id=%s", job_id)
            try:
                job = db.get_job(job_id)
                if not job or job["status"] != "queued":
                    logger.warning("JobManager._worker: job_id=%s not runnable (status=%s); skipping", job_id, job.get("status") if job else "missing")
                    continue
                jp = JobProcess(job, self.bus, loop)
                async with self._lock:
                    self._procs[job_id] = jp
                logger.info("JobManager._worker: starting process for job_id=%s", job_id)
                try:
                    jp.start()
                    rc = await jp.wait()
                    elapsed_s = time.monotonic() - start_ts
                    logger.info("JobManager._worker: job_id=%s finished (rc=%s) in %.1fs", job_id, rc, elapsed_s)
                finally:
                    async with self._lock:
                        self._procs.pop(job_id, None)
            except asyncio.CancelledError:
                logger.info("JobManager._worker: cancelled while processing job_id=%s", job_id)
                raise
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception("JobManager._worker: unexpected error for job_id=%s: %s", job_id, exc)
                db.update_job(
                    job_id,
                    status="failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                    finished_at=db.now_iso(),
                )
            finally:
                self._queue.task_done()
                logger.debug("JobManager._worker: task_done for job_id=%s", job_id)


_manager: JobManager | None = None


def manager(bus: EventBus | None = None) -> JobManager:
    global _manager
    if _manager is None:
        assert bus is not None, "first call must supply an EventBus"
        logger.debug("manager: creating singleton JobManager")
        _manager = JobManager(bus)
    return _manager


def reset_for_tests() -> None:
    global _manager
    logger.debug("reset_for_tests: resetting singleton JobManager")
    _manager = None
    """Owns the asyncio queue and process registry."""

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._procs: dict[str, JobProcess] = {}
        self._workers: list[asyncio.Task] = []
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        cfg = load_config()
        max_workers = int(cfg.get("concurrency", {}).get("max_running_jobs", 2))
        for _ in range(max(1, max_workers)):
            self._workers.append(asyncio.create_task(self._worker()))

    async def shutdown(self) -> None:
        for t in self._workers:
            t.cancel()
        for jp in list(self._procs.values()):
            try:
                jp.cancel()
            except Exception:
                pass
        self._workers.clear()
        self._started = False

    async def submit(self, payload: dict[str, Any]) -> str:
        job_id = "job_" + secrets.token_hex(8)
        change_id = payload["change_id"]
        mode = payload.get("mode", "live")
        events_path, cassette_path = prepare_job_paths(change_id, mode)
        record = {
            "id": job_id,
            "change_id": change_id,
            "parent_job_id": payload.get("parent_job_id"),
            "status": "queued",
            "mode": mode,
            "runner": payload["runner"],
            "model": payload.get("model"),
            "repo": payload["repo"],
            "ado_url": payload.get("ado_url"),
            "story_file": payload.get("story_file"),
            "extra_context": payload.get("extra_context"),
            "submitted_at": db.now_iso(),
            "events_path": events_path,
            "cassette_path": cassette_path,
        }
        db.insert_job(record)
        await self._queue.put(job_id)
        return job_id

    async def cancel(self, job_id: str) -> bool:
        job = db.get_job(job_id)
        if not job:
            return False
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return False
        jp = self._procs.get(job_id)
        if jp is not None:
            ok = jp.cancel()
            if ok:
                db.update_job(job_id, status="cancelled")
            return ok
        # Queued but not yet started: mark cancelled directly.
        db.update_job(job_id, status="cancelled", finished_at=db.now_iso())
        return True

    async def _worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job_id = await self._queue.get()
            try:
                job = db.get_job(job_id)
                if not job or job["status"] != "queued":
                    continue
                jp = JobProcess(job, self.bus, loop)
                async with self._lock:
                    self._procs[job_id] = jp
                try:
                    jp.start()
                    await jp.wait()
                finally:
                    async with self._lock:
                        self._procs.pop(job_id, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover — defensive
                db.update_job(
                    job_id,
                    status="failed",
                    error_message=f"{type(exc).__name__}: {exc}",
                    finished_at=db.now_iso(),
                )
            finally:
                self._queue.task_done()
