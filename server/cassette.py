"""Hermetic-mode subprocess recorder.

Appends one JSONL record per CLI invocation (cmd/args/stdin/stdout/stderr/exit/duration).
Replay is intentionally NOT implemented — recording only.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_lock = threading.Lock()


def cassette_path() -> Path | None:
    p = os.environ.get("AGENT_RUNNER_CASSETTE")
    return Path(p) if p else None


def record(
    *,
    cmd: list[str],
    stdin: str | None,
    stdout: str | None,
    stderr: str | None,
    exit_code: int,
    duration_ms: int,
    stage: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    path = cassette_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "cmd": list(cmd or []),
        "stage": stage,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    if extra:
        record.update(extra)
    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
