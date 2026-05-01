"""Load/save ~/.agent-runner/config.json with defaults and validation."""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from .paths import RUNNER_ROOT, config_path, data_dir

logger = logging.getLogger(__name__)


def _default_opik_config() -> dict[str, str]:
    return {
        "dashboard_url": os.environ.get("OPIK_DASHBOARD_URL", ""),
        "workspace_name": os.environ.get("OPIK_WORKSPACE", ""),
        "project_id": os.environ.get("OPIK_PROJECT_ID", ""),
        "project_name": os.environ.get("OPIK_PROJECT_NAME", "agent-runner"),
    }


DEFAULTS: dict[str, Any] = {
    "api": {"host": "127.0.0.1", "port": 8742},
    "defaults": {
        "runner": "claude",
        "model": None,
        "mode": "live",
        "copilot_effort": None,
        "skip_materialize": False,
    },
    "paths": {
        "runner_root": str(RUNNER_ROOT),
        "agent_context": str(RUNNER_ROOT / "agent-context"),
        "data_dir": str(data_dir()),
    },
    "concurrency": {"max_running_jobs": 2},
    "opik": _default_opik_config(),
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    path = config_path()
    logger.debug("load_config: reading from %s", path)
    if not path.exists():
        logger.info("load_config: config file absent; writing defaults to %s", path)
        cfg = deepcopy(DEFAULTS)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        return cfg
    try:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("load_config: successfully parsed %s", path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load_config: could not read/parse %s (%s); using defaults", path, exc)
        on_disk = {}
    merged = _deep_merge(DEFAULTS, on_disk if isinstance(on_disk, dict) else {})
    logger.debug("load_config: merged config api.port=%s", merged.get("api", {}).get("port"))
    return merged


def validate_config(cfg: dict) -> list[str]:
    """Return a list of human-readable validation errors. Empty = valid."""
    logger.debug("validate_config called")
    errors: list[str] = []
    api = cfg.get("api") or {}
    port = api.get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append("api.port must be an integer between 1 and 65535")
    host = api.get("host")
    if not isinstance(host, str) or not host:
        errors.append("api.host must be a non-empty string")
    defaults = cfg.get("defaults") or {}
    runner = defaults.get("runner")
    if runner not in (None, "claude", "copilot", "gemini"):
        errors.append("defaults.runner must be one of: claude, copilot, gemini")
    mode = defaults.get("mode")
    if mode not in (None, "live", "hermetic"):
        errors.append("defaults.mode must be 'live' or 'hermetic'")
    conc = cfg.get("concurrency") or {}
    mr = conc.get("max_running_jobs")
    if not isinstance(mr, int) or mr < 1:
        errors.append("concurrency.max_running_jobs must be a positive integer")
    opik = cfg.get("opik") or {}
    for key in ("dashboard_url", "workspace_name", "project_id", "project_name"):
        value = opik.get(key, "")
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"opik.{key} must be a string")
    dashboard_url = (opik.get("dashboard_url") or "").strip()
    if dashboard_url:
        parsed = urlparse(dashboard_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors.append("opik.dashboard_url must be an absolute http(s) URL")
    if errors:
        logger.warning("validate_config: %d error(s): %s", len(errors), errors)
    else:
        logger.debug("validate_config: config is valid")
    return errors


def save_config(cfg: dict) -> dict:
    """Deep-merge ``cfg`` over current on-disk config, validate, and persist.

    Returns the merged config that was saved.
    """
    logger.info("save_config: merging and persisting config changes")
    current = load_config()
    merged = _deep_merge(current, cfg) if cfg else current
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("save_config: config written to %s", path)
    return merged
