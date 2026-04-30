"""Filesystem layout for the agent-runner local server."""
from __future__ import annotations

import os
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parent.parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
AGENT_SOURCES_ROOT = RUNNER_ROOT / "agent-sources"
EVAL_STORIES_ROOT = RUNNER_ROOT / "eval" / "stories"
GUI_ROOT = RUNNER_ROOT / "gui"


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


def events_path_for(change_id: str) -> Path:
    p = AGENT_CONTEXT_ROOT / change_id
    p.mkdir(parents=True, exist_ok=True)
    return p / "events.jsonl"


def user_questions_path_for(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "intake" / "user_questions.json"


def user_responses_path_for(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "intake" / "user_responses.json"
