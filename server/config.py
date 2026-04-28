"""Configuration management for agent-runner server.

Reads/writes ~/.agent-runner/config.json with sensible defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG: dict[str, Any] = {
    "api": {"host": "127.0.0.1", "port": 8742},
    "defaults": {"runner": "claude", "model": None, "mode": "live"},
    "paths": {
        "runner_root": None,
        "agent_context": None,
        "data_dir": "~/.agent-runner",
    },
    "concurrency": {"max_running_jobs": 2},
}

_RUNNER_ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    raw = os.environ.get("AGENT_RUNNER_DATA_DIR", "~/.agent-runner")
    return Path(raw).expanduser()


def config_path() -> Path:
    return _data_dir() / "config.json"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> dict[str, Any]:
    """Return merged config: defaults + persisted file (if it exists)."""
    cfg = _deep_merge({}, _DEFAULT_CONFIG)
    # Fill in computed defaults for paths
    cfg["paths"]["runner_root"] = str(_RUNNER_ROOT)
    cfg["paths"]["agent_context"] = str(_RUNNER_ROOT / "agent-context")
    cfg["paths"]["data_dir"] = str(_data_dir())

    path = config_path()
    if path.exists():
        try:
            disk = json.loads(path.read_text(encoding="utf-8"))
            cfg = _deep_merge(cfg, disk)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config to disk after validation."""
    _validate_config(cfg)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _validate_config(cfg: dict[str, Any]) -> None:
    """Raise ValueError for obviously invalid config values."""
    api = cfg.get("api", {})
    port = api.get("port")
    if port is not None:
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(f"Invalid port: {port!r}. Must be an integer between 1 and 65535.")

    host = api.get("host")
    if host is not None and not isinstance(host, str):
        raise ValueError("api.host must be a string.")

    concurrency = cfg.get("concurrency", {})
    max_jobs = concurrency.get("max_running_jobs")
    if max_jobs is not None:
        if not isinstance(max_jobs, int) or max_jobs < 1:
            raise ValueError(f"concurrency.max_running_jobs must be a positive integer, got {max_jobs!r}.")
