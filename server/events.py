"""JSON event schema, emitter, and file tailer for agent-runner jobs."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ── Event emission (used inside run.py subprocess) ────────────────────────────

class EventEmitter:
    """Appends structured JSON events to a JSONL file.

    Thread-safe. No-op when path is None (CLI-only usage).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        if self._path is None:
            return
        with self._lock:
            self._seq += 1
            event = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **event,
                "seq": self._seq,
            }
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError:
                pass

    def stage_start(self, stage: str) -> None:
        self.emit({"type": "stage.start", "stage": stage})

    def stage_end(self, stage: str, status: str = "ok") -> None:
        self.emit({"type": "stage.end", "stage": stage, "status": status})

    def log(self, msg: str, level: str = "info") -> None:
        self.emit({"type": "log", "level": level, "msg": msg})

    def job_start(self, change_id: str) -> None:
        self.emit({"type": "job.start", "change_id": change_id})

    def job_end(self, status: str, exit_code: int = 0) -> None:
        self.emit({"type": "job.end", "status": status, "exit_code": exit_code})

    def metrics(self, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self.emit({
            "type": "metrics",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
        })


# Singleton emitter — initialized from env var in run.py
_GLOBAL_EMITTER: EventEmitter = EventEmitter()


def get_emitter() -> EventEmitter:
    return _GLOBAL_EMITTER


def init_emitter_from_env() -> EventEmitter:
    """Initialize the global emitter from AGENT_RUNNER_EVENT_LOG env var."""
    global _GLOBAL_EMITTER
    path = os.environ.get("AGENT_RUNNER_EVENT_LOG")
    _GLOBAL_EMITTER = EventEmitter(path)
    return _GLOBAL_EMITTER


# ── Event file tailer (used by SSE route in server) ──────────────────────────

def read_events_from_file(path: str | Path) -> list[dict[str, Any]]:
    """Read all events from a JSONL file, skipping malformed lines."""
    events: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return events
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return events


class EventTailer:
    """Tails a JSONL event file and broadcasts new events to async subscribers.

    Usage:
        tailer = EventTailer(path)
        queue = tailer.subscribe()
        asyncio.create_task(tailer.run())
        async for event in iter_queue(queue):
            ...
    """

    _POLL_INTERVAL = 0.25  # seconds

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._stopped = False

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def stop(self) -> None:
        self._stopped = True

    async def run(self, on_event: Callable[[dict], None] | None = None) -> None:
        """Poll the file for new events and broadcast them."""
        offset = 0
        while not self._stopped:
            new_events = self._read_new(offset)
            for event in new_events:
                offset += 1
                async with self._lock:
                    for q in list(self._subscribers):
                        await q.put(event)
                if on_event:
                    on_event(event)
                # Stop tailing when job ends
                if event.get("type") == "job.end":
                    self._stopped = True
                    break
            if self._stopped:
                break
            await asyncio.sleep(self._POLL_INTERVAL)

        # Signal end to all subscribers
        async with self._lock:
            for q in list(self._subscribers):
                await q.put(None)  # sentinel

    def _read_new(self, offset: int) -> list[dict[str, Any]]:
        """Read events from file starting at 'offset' (event count, not byte)."""
        all_events = read_events_from_file(self._path)
        return all_events[offset:]
