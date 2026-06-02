"""Filesystem layout for the agent-runner local server."""
from __future__ import annotations

import re
from pathlib import Path

from core.runtime_paths import (
    agent_context_root,
    data_dir as runtime_data_dir,
    eval_agent_reports_root,
    eval_benchmarks_root,
    eval_data_root,
    eval_reports_root,
    logs_root,
)

RUNNER_ROOT = Path(__file__).resolve().parent.parent
AGENT_CONTEXT_ROOT = agent_context_root()
LOGS_ROOT = logs_root()
AGENT_SOURCES_ROOT = RUNNER_ROOT / "agent-definition-source"
EVAL_STORIES_ROOT = RUNNER_ROOT / "eval" / "stories"
GUI_ROOT = RUNNER_ROOT / "gui"
LEGACY_AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
LEGACY_LOGS_ROOT = RUNNER_ROOT / "logs"
LEGACY_EVAL_REPORTS_ROOT = RUNNER_ROOT / "eval" / "reports"
LEGACY_EVAL_BENCHMARKS_ROOT = RUNNER_ROOT / "eval" / "benchmarks"

_ALLOWED_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


def safe_id(value: str) -> str:
    """Validate *value* as a safe path component (no slashes, no traversal)."""
    if not value or not _ALLOWED_ID.fullmatch(value):
        raise ValueError(f"unsafe id: {value!r}")
    return value


def data_dir() -> Path:
    """Return the per-user data directory.

    Honors the AGENT_RUNNER_DATA_DIR env var so tests can redirect it.
    """
    p = runtime_data_dir()
    p.mkdir(parents=True, exist_ok=True)
    (p / "cassettes").mkdir(exist_ok=True)
    (p / "memory").mkdir(exist_ok=True)
    return p


def eval_agent_datasets_dir() -> Path:
    return eval_data_root(create=True)


def eval_agent_reports_dir() -> Path:
    return eval_agent_reports_root(create=True)


def eval_reports_dir() -> Path:
    return eval_reports_root(create=True)


def eval_benchmarks_dir() -> Path:
    return eval_benchmarks_root(create=True)


def db_path() -> Path:
    return data_dir() / "jobs.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def cassettes_dir() -> Path:
    d = data_dir() / "cassettes"
    d.mkdir(exist_ok=True)
    return d


def logs_dir_for(change_id: str) -> Path:
    p = logs_root(create=True) / change_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def events_path_for(change_id: str) -> Path:
    return logs_dir_for(change_id) / "events.jsonl"


def events_path_for_job(job_id: str) -> Path:
    p = data_dir() / "events" / safe_id(job_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / "events.jsonl"


def job_inputs_dir(job_id: str) -> Path:
    p = data_dir() / "job-inputs" / safe_id(job_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def manual_story_file_path_for(job_id: str) -> Path:
    return job_inputs_dir(job_id) / "manual_story.json"


def legacy_events_path_for(change_id: str) -> Path:
    p = agent_context_root(create=True) / change_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "events.jsonl"


def user_questions_path_for(change_id: str) -> Path:
    return agent_context_root() / change_id / "intake" / "user_questions.json"


def user_responses_path_for(change_id: str) -> Path:
    return agent_context_root() / change_id / "intake" / "user_responses.json"


# ─────────────────────────────────────────────────────────────────────────────
# Escalation paths — general-purpose human-in-the-loop coordination
# ─────────────────────────────────────────────────────────────────────────────


def escalations_dir_for(change_id: str) -> Path:
    return agent_context_root() / change_id / "escalations"


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
