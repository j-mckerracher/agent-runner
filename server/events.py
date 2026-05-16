"""JSONL event log: emitter (CLI side) + tailer / pubsub (server side)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Emitter — used by run.py / run_cmds.py inside the worker subprocess.
# ─────────────────────────────────────────────────────────────────────────────


class EventEmitter:
    """Append-only JSONL writer with monotonic seq. Process-local."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        # If the file exists from a previous run, advance seq beyond its tail
        # so writes don't collide with replayed history.
        if self.path.exists():
            try:
                last = 0
                with self.path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            last = max(last, int(obj.get("seq", 0)))
                        except Exception:
                            pass
                self._seq = last
                logger.debug("EventEmitter: resumed from seq=%d for %s", self._seq, self.path)
            except OSError as exc:
                logger.warning("EventEmitter: could not read existing log %s: %s", self.path, exc)
        else:
            logger.debug("EventEmitter: new log file %s", self.path)

    def emit(self, type: str, **fields: Any) -> dict:
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "type": type,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
            logger.debug("EventEmitter.emit: seq=%d type=%s", record["seq"], type)
            return record


_default: EventEmitter | None = None
_default_lock = threading.Lock()


def get_emitter() -> EventEmitter | None:
    """Return a process-wide emitter when AGENT_RUNNER_EVENT_LOG is set, else None."""
    global _default
    path = os.environ.get("AGENT_RUNNER_EVENT_LOG")
    if not path:
        return None
    with _default_lock:
        if _default is None or str(_default.path) != path:
            logger.debug("get_emitter: creating/refreshing emitter for %s", path)
            _default = EventEmitter(path)
        return _default


def emit(type: str, **fields: Any) -> None:
    em = get_emitter()
    if em is not None:
        em.emit(type, **fields)


class EventEmitHandler(logging.Handler):
    """Bridges stdlib logging → structured `log` events via EventEmitter.

    No-op when AGENT_RUNNER_EVENT_LOG is not set (CLI / test mode).
    Skips this module's own logger to prevent recursion.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return
        em = get_emitter()
        if em is None:
            return
        try:
            em.emit(
                "log",
                level=record.levelname.lower(),
                logger=record.name,
                msg=self.format(record),
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Tailer / pubsub — used by the FastAPI server to multiplex over SSE.
# ─────────────────────────────────────────────────────────────────────────────


def read_all(path: str | os.PathLike) -> list[dict]:
    p = Path(path)
    logger.debug("read_all: reading events from %s", p)
    if not p.exists():
        logger.debug("read_all: file does not exist: %s", p)
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("read_all: skipping malformed line in %s: %s", p, exc)
                continue
    logger.debug("read_all: %d event(s) read from %s", len(out), p)
    return out


class EventBus:
    """Per-job pubsub. Subscribers receive new events via asyncio.Queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # job_id -> list[asyncio.Queue]
        self._subs: dict[str, list[asyncio.Queue]] = {}
        # job_id -> set of "done" markers
        self._done: set[str] = set()
        logger.debug("EventBus: initialised")

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.setdefault(job_id, []).append(q)
        sub_count = len(self._subs.get(job_id, []))
        logger.debug("EventBus.subscribe: job_id=%s (%d subscriber(s) now)", job_id, sub_count)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            lst = self._subs.get(job_id) or []
            if q in lst:
                lst.remove(q)
            if not lst:
                self._subs.pop(job_id, None)
        logger.debug("EventBus.unsubscribe: job_id=%s", job_id)

    def publish(self, job_id: str, event: dict, loop: asyncio.AbstractEventLoop | None = None) -> None:
        with self._lock:
            queues = list(self._subs.get(job_id, ()))
        if not queues:
            logger.debug("EventBus.publish: no subscribers for job_id=%s (type=%s)", job_id, event.get("type"))
            return
        logger.debug("EventBus.publish: job_id=%s type=%s → %d queue(s)", job_id, event.get("type"), len(queues))
        for q in queues:
            if loop is None:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("EventBus.publish: queue full for job_id=%s; event dropped", job_id)
            else:
                loop.call_soon_threadsafe(q.put_nowait, event)

    def mark_done(self, job_id: str) -> None:
        with self._lock:
            self._done.add(job_id)
        logger.debug("EventBus.mark_done: job_id=%s", job_id)

    def is_done(self, job_id: str) -> bool:
        with self._lock:
            result = job_id in self._done
        return result


class FileTailer(threading.Thread):
    """Tails a JSONL file and publishes each new record to an EventBus.

    Stops when the bus marks the job_id done OR when ``stop()`` is called.
    """

    def __init__(self, path: str | os.PathLike, job_id: str, bus: EventBus,
                 loop: asyncio.AbstractEventLoop, on_event=None) -> None:
        super().__init__(daemon=True, name=f"tailer-{job_id}")
        self.path = Path(path)
        self.job_id = job_id
        self.bus = bus
        self.loop = loop
        self.on_event = on_event
        self._stop = threading.Event()
        logger.debug("FileTailer: created for job_id=%s path=%s", job_id, path)

    def stop(self) -> None:
        logger.debug("FileTailer.stop: signalling thread for job_id=%s", self.job_id)
        self._stop.set()

    def run(self) -> None:
        logger.info("FileTailer.run: starting for job_id=%s path=%s", self.job_id, self.path)
        # Wait for file to appear (subprocess hasn't started yet, possibly).
        for attempt in range(200):
            if self.path.exists() or self._stop.is_set():
                if self.path.exists():
                    logger.debug("FileTailer.run: file appeared after %d poll(s)", attempt)
                break
            time.sleep(0.05)
        else:
            logger.warning("FileTailer.run: file never appeared: %s", self.path)

        events_seen = 0
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                buf = ""
                while not self._stop.is_set():
                    chunk = fh.read()
                    if chunk:
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                evt = json.loads(line)
                            except json.JSONDecodeError as exc:
                                logger.warning("FileTailer.run: malformed line for job_id=%s: %s", self.job_id, exc)
                                continue
                            events_seen += 1
                            logger.debug("FileTailer.run: job_id=%s seq=%s type=%s", self.job_id, evt.get("seq"), evt.get("type"))
                            self.bus.publish(self.job_id, evt, loop=self.loop)
                            if self.on_event is not None:
                                try:
                                    self.on_event(evt)
                                except Exception as exc:
                                    logger.warning("FileTailer.run: on_event callback raised: %s", exc)
                            if evt.get("type") == "job.end":
                                logger.info("FileTailer.run: job.end received for job_id=%s after %d event(s)", self.job_id, events_seen)
                                self._stop.set()
                    else:
                        time.sleep(0.1)
        except OSError as exc:
            logger.error("FileTailer.run: I/O error for job_id=%s: %s", self.job_id, exc)
        finally:
            logger.info("FileTailer.run: finishing for job_id=%s (%d event(s) processed)", self.job_id, events_seen)
            self.bus.mark_done(self.job_id)
            self.bus.publish(self.job_id, {"type": "stream.end"}, loop=self.loop)


def aggregate(events: Iterable[dict]) -> dict[str, Any]:
    """Compute summary aggregates from an event sequence."""
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    current_stage: str | None = None
    final_status: str | None = None
    for e in events:
        t = e.get("type")
        if t == "metrics":
            tokens_in += int(e.get("tokens_in") or 0)
            tokens_out += int(e.get("tokens_out") or 0)
            try:
                cost_usd += float(e.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass
        elif t == "stage.start":
            current_stage = e.get("stage")
        elif t == "stage.end":
            current_stage = None
        elif t == "job.end":
            final_status = e.get("status")
    result = {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 6),
        "current_stage": current_stage,
        "final_status": final_status,
    }
    logger.debug("aggregate: tokens_in=%d tokens_out=%d cost_usd=%f final_status=%s", tokens_in, tokens_out, cost_usd, final_status)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Emitter — used by run.py / run_cmds.py inside the worker subprocess.
# ─────────────────────────────────────────────────────────────────────────────


class EventEmitter:
    """Append-only JSONL writer with monotonic seq. Process-local."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        # If the file exists from a previous run, advance seq beyond its tail
        # so writes don't collide with replayed history.
        if self.path.exists():
            try:
                last = 0
                with self.path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            last = max(last, int(obj.get("seq", 0)))
                        except Exception:
                            pass
                self._seq = last
            except OSError:
                pass

    def emit(self, type: str, **fields: Any) -> dict:
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "type": type,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
            return record
