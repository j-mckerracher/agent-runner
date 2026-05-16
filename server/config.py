"""Load/save ~/.agent-runner/config.json with defaults and validation."""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from .paths import RUNNER_ROOT, config_path, data_dir
from core.runner_models import KNOWN_RUNNERS, RUNNER_ALIAS_LLM_OPTION_KEYS, RUNNER_MODEL_CHOICES

logger = logging.getLogger(__name__)

MAX_REPO_CUSTOM_VALUES = 200


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
    },
    "agent_model_defaults": {},
    "runner_aliases": {},
    "paths": {
        "runner_root": str(RUNNER_ROOT),
        "agent_context": str(RUNNER_ROOT / "agent-context"),
        "data_dir": str(data_dir()),
    },
    "concurrency": {"max_running_jobs": 2},
    "opik": _default_opik_config(),
    "repo_paths": {
        "base_dir": "",
        "custom_values": [],
    },
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
    if runner is not None:
        valid_runners = set(KNOWN_RUNNERS) | set((cfg.get("runner_aliases") or {}).keys())
        if not isinstance(runner, str) or runner not in valid_runners:
            errors.append(
                f"defaults.runner must be one of: {', '.join(sorted(valid_runners))}"
            )
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

    repo_paths = cfg.get("repo_paths")
    if not isinstance(repo_paths, dict):
        errors.append("repo_paths must be a dict")
    else:
        base_dir = repo_paths.get("base_dir", "")
        if not isinstance(base_dir, str):
            errors.append("repo_paths.base_dir must be a string")
        custom_values = repo_paths.get("custom_values", [])
        if not isinstance(custom_values, list):
            errors.append("repo_paths.custom_values must be a list of strings")
        else:
            if len(custom_values) > MAX_REPO_CUSTOM_VALUES:
                errors.append(
                    f"repo_paths.custom_values must contain at most {MAX_REPO_CUSTOM_VALUES} entries"
                )
            for index, value in enumerate(custom_values):
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"repo_paths.custom_values[{index}] must be a non-empty string")

    runner_aliases = cfg.get("runner_aliases") or {}
    if not isinstance(runner_aliases, dict):
        errors.append("runner_aliases must be a dict")
    else:
        for alias_name, alias_def in runner_aliases.items():
            if not isinstance(alias_name, str):
                errors.append(f"runner_aliases key must be a string, got {type(alias_name)}")
                continue
            if not isinstance(alias_def, dict):
                errors.append(f"runner_aliases[{alias_name}] must be a dict with provider and model")
                continue
            provider = alias_def.get("provider")
            model = alias_def.get("model")
            if not isinstance(provider, str) or not provider:
                errors.append(f"runner_aliases[{alias_name}].provider must be a non-empty string")
            if not isinstance(model, str) or not model:
                errors.append(f"runner_aliases[{alias_name}].model must be a non-empty string")
            api_key_env = alias_def.get("api_key_env")
            if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env):
                errors.append(
                    f"runner_aliases[{alias_name}].api_key_env must be a non-empty string when set"
                )
            for key in ("base_url", "api_version"):
                value = alias_def.get(key)
                if value is not None and (not isinstance(value, str) or not value):
                    errors.append(
                        f"runner_aliases[{alias_name}].{key} must be a non-empty string when set"
                    )
            extra_headers = alias_def.get("extra_headers")
            if extra_headers is not None:
                if not isinstance(extra_headers, dict):
                    errors.append(f"runner_aliases[{alias_name}].extra_headers must be a dict")
                elif any(not isinstance(k, str) or not isinstance(v, str) for k, v in extra_headers.items()):
                    errors.append(
                        f"runner_aliases[{alias_name}].extra_headers must be a dict[str, str]"
                    )
            extra_body = alias_def.get("litellm_extra_body")
            if extra_body is not None and not isinstance(extra_body, dict):
                errors.append(f"runner_aliases[{alias_name}].litellm_extra_body must be a dict")
            num_retries = alias_def.get("num_retries")
            if num_retries is not None and (not isinstance(num_retries, int) or isinstance(num_retries, bool) or num_retries < 0):
                errors.append(
                    f"runner_aliases[{alias_name}].num_retries must be a non-negative integer"
                )
            for key in ("retry_multiplier", "retry_min_wait", "retry_max_wait", "timeout"):
                value = alias_def.get(key)
                if value is not None and (
                    not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0
                ):
                    errors.append(
                        f"runner_aliases[{alias_name}].{key} must be a positive number"
                    )
            allowed_alias_keys = {"provider", "model", "api_key_env", *RUNNER_ALIAS_LLM_OPTION_KEYS}
            unknown_keys = sorted(set(alias_def.keys()) - allowed_alias_keys)
            if unknown_keys:
                errors.append(
                    f"runner_aliases[{alias_name}] has unsupported keys: {', '.join(unknown_keys)}"
                )

    valid_runners = set(KNOWN_RUNNERS) | set(runner_aliases.keys())
    agent_defaults = cfg.get("agent_model_defaults") or {}
    if not isinstance(agent_defaults, dict):
        errors.append("agent_model_defaults must be a dict")
    else:
        for agent_name, runner_models in agent_defaults.items():
            if not isinstance(agent_name, str):
                errors.append(f"agent_model_defaults key must be a string, got {type(agent_name)}")
                continue
            if not isinstance(runner_models, dict):
                errors.append(f"agent_model_defaults[{agent_name}] must be a dict")
                continue
            for runner_key, model in runner_models.items():
                if runner_key not in valid_runners:
                    errors.append(
                        f"agent_model_defaults[{agent_name}][{runner_key}] has invalid "
                        f"runner '{runner_key}'; must be one of: {', '.join(sorted(valid_runners))}"
                    )
                elif not isinstance(model, str):
                    errors.append(f"agent_model_defaults[{agent_name}][{runner_key}] model must be a string")
                elif runner_key in RUNNER_MODEL_CHOICES:
                    valid_models = RUNNER_MODEL_CHOICES[runner_key]
                    if model not in valid_models:
                        errors.append(
                            f"agent_model_defaults[{agent_name}][{runner_key}]={model} "
                            f"is not valid for runner={runner_key}"
                        )

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
