"""Runtime path resolution for local Agent Workbench state."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Agent Workbench"
LINUX_APP_DIR = "agent-workbench"


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def default_data_dir() -> Path:
    """Return the native per-user data directory for this platform."""
    if sys.platform == "darwin":
        return _expand(Path.home() / "Library" / "Application Support" / APP_NAME)
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return _expand(base / APP_NAME)
    return _expand(Path.home() / ".local" / "share" / LINUX_APP_DIR)


def data_dir(*, create: bool = True) -> Path:
    """Return the runtime data directory, honoring AGENT_RUNNER_DATA_DIR."""
    override = os.environ.get("AGENT_RUNNER_DATA_DIR")
    path = _expand(override) if override else default_data_dir()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def load_data_dir_override_from_env_file(path: Path) -> None:
    """Load only AGENT_RUNNER_DATA_DIR from a dotenv-style file."""
    if os.environ.get("AGENT_RUNNER_DATA_DIR") or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "AGENT_RUNNER_DATA_DIR":
            os.environ["AGENT_RUNNER_DATA_DIR"] = value.strip().strip("\"'")
            return


def agent_context_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "agent-context"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def logs_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "logs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def worktrees_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "worktrees"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def locks_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "locks"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def eval_data_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "eval" / "agent_datasets"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def eval_reports_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "eval" / "reports"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def eval_agent_reports_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "eval" / "agent_reports"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def eval_benchmarks_root(*, create: bool = False) -> Path:
    path = data_dir(create=create) / "eval" / "benchmarks"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
