"""Filesystem layout for the agent-runner local server."""
from __future__ import annotations

import os
import re
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parent.parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
LOGS_ROOT = RUNNER_ROOT / "logs"
AGENT_SOURCES_ROOT = RUNNER_ROOT / "agent-definition-source"
EVAL_STORIES_ROOT = RUNNER_ROOT / "eval" / "stories"
GUI_ROOT = RUNNER_ROOT / "gui"

_ALLOWED_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


def safe_id(value: str) -> str:
    """Validate *value* as a safe path component (no slashes, no traversal)."""
    if not value or not _ALLOWED_ID.fullmatch(value):
        raise ValueError(f"unsafe id: {value!r}")
    return value


def data_dir() -> Path:
    """Return the per-user data directory (~/.agent-runner by default).

    Honors the AGENT_RUNNER_DATA_DIR env var so tests can redirect it.
    """
    override = os.environ.get("AGENT_RUNNER_DATA_DIR")
    if override:
        p = Path(override).expanduser().resolve()
    else:
        p = Path.home() / ".agent-runner"
    p.mkdir(parents=True, exist_ok=True)
    (p / "cassettes").mkdir(exist_ok=True)
    (p / "memory").mkdir(exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "jobs.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def cassettes_dir() -> Path:
    d = data_dir() / "cassettes"
    d.mkdir(exist_ok=True)
    return d


def logs_dir_for(change_id: str) -> Path:
    p = LOGS_ROOT / change_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def events_path_for(change_id: str) -> Path:
    return logs_dir_for(change_id) / "events.jsonl"


def legacy_events_path_for(change_id: str) -> Path:
    p = AGENT_CONTEXT_ROOT / change_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "events.jsonl"


def user_questions_path_for(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "intake" / "user_questions.json"


def user_responses_path_for(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "intake" / "user_responses.json"


# ─────────────────────────────────────────────────────────────────────────────
# Escalation paths — general-purpose human-in-the-loop coordination
# ─────────────────────────────────────────────────────────────────────────────


def escalations_dir_for(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "escalations"


def conversation_dir_for(change_id: str, conversation_id: str) -> Path:
    return escalations_dir_for(change_id) / safe_id(conversation_id)


def escalation_request_path_for(
    change_id: str,
    conversation_id: str,
    escalation_id: str,
) -> Path:
    return conversation_dir_for(change_id, conversation_id) / "turns" / f"{safe_id(escalation_id)}.request.json"


def escalation_response_path_for(
    change_id: str,
    conversation_id: str,
    escalation_id: str,
) -> Path:
    return conversation_dir_for(change_id, conversation_id) / "turns" / f"{safe_id(escalation_id)}.response.json"


def escalation_transcript_path_for(change_id: str, conversation_id: str) -> Path:
    return conversation_dir_for(change_id, conversation_id) / "transcript.jsonl"

