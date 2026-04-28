"""Hermetic-mode cassette recorder for CLI subprocess invocations.

When AGENT_RUNNER_CASSETTE env var is set to a file path, every CLI subprocess
call is recorded as a JSON line in that file.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CassetteRecorder:
    """Appends CLI invocation records to a JSONL cassette file.

    Thread-safe. No-op when path is None.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()

    def record(
        self,
        *,
        stage: str,
        cmd: str,
        args: list[str],
        stdin: str | None,
        stdout: str,
        stderr: str,
        exit_code: int,
        duration_ms: float,
    ) -> None:
        if self._path is None:
            return
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stage": stage,
            "cmd": cmd,
            "args": args,
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                pass


# Singleton recorder — initialized from env var
_GLOBAL_RECORDER: CassetteRecorder = CassetteRecorder()


def get_recorder() -> CassetteRecorder:
    return _GLOBAL_RECORDER


def init_recorder_from_env() -> CassetteRecorder:
    """Initialize global recorder from AGENT_RUNNER_CASSETTE env var."""
    global _GLOBAL_RECORDER
    path = os.environ.get("AGENT_RUNNER_CASSETTE")
    _GLOBAL_RECORDER = CassetteRecorder(path)
    return _GLOBAL_RECORDER
