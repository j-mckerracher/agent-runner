"""Event contract v1 for the Agent Runner.

Events are emitted to stdout as `##EVENT## <json>` lines (legacy
compatibility) and optionally to a JSON-lines file (new). Consumers
tolerate the known major version only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

EVENT_VERSION = "1"
EVENT_PREFIX = "##EVENT##"

# Known event kinds. Not exhaustive — additive fields are allowed without
# bumping the event version; adding a new `kind` is not a breaking change.
KNOWN_KINDS = {
    "run.start",
    "run.end",
    "stage.start",
    "stage.end",
    "eval.pass",
    "eval.fail",
    "retry",
    "escalate",
    "artifact.write",
    "workflow_error",
    "agent_dispatch",
    "agent_result",
    "evaluation_result",
    "eval_attempt",
    "escalation_start",
    "escalation_end",
    "cassette.record",
    "cassette.replay",
    "cassette.miss",
    "container.start",
    "container.end",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_event_line(
    kind: str,
    *,
    run_id: Optional[str] = None,
    stage: Optional[str] = None,
    **data: Any,
) -> str:
    """Return the stdout line to emit for an event (newline terminated).

    The caller is responsible for writing; this lets event emission be
    captured or redirected by harness code without taking over stdout.
    """
    payload: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "ts": _iso_now(),
        "kind": kind,
    }
    if run_id is not None:
        payload["run_id"] = run_id
    if stage is not None:
        payload["stage"] = stage
    if data:
        payload["data"] = data
    return f"{EVENT_PREFIX} {json.dumps(payload, default=str)}"


def parse_event_line(line: str) -> Optional[dict[str, Any]]:
    """Parse a single stdout line as an event. Returns None if not an event."""
    stripped = line.strip()
    if not stripped.startswith(EVENT_PREFIX):
        return None
    _, _, json_text = stripped.partition(" ")
    if not json_text:
        return None
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("event_version", "1")
    if version != EVENT_VERSION:
        # Unknown major version — fail closed per contract.
        raise ValueError(
            f"Unknown event_version {version!r}; expected {EVENT_VERSION!r}"
        )
    return payload
