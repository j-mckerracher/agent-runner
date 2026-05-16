import logging
import json as _json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from fastapi import APIRouter, HTTPException

from ..config import load_config, save_config, validate_config
from ..runner_models_facade import runner_choices

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _opik_base_url(raw_url: str) -> str:
    """Return scheme+host[:port], stripping any path."""
    parsed = urlparse(raw_url.strip().rstrip("/"))
    return f"{parsed.scheme}://{parsed.netloc}"


def _opik_workspace_from_url(raw_url: str) -> str:
    """Extract the workspace name from the first non-empty path segment.

    e.g. http://localhost:5173/default/home  → "default"
         https://www.comet.com/opik          → ""  (fall back to caller)
    """
    path_parts = [p for p in urlparse(raw_url.strip()).path.split("/") if p]
    return path_parts[0] if path_parts else ""


def _opik_rest(method: str, url: str, *, workspace: str = "", body: dict | None = None) -> Any:
    """Minimal HTTP helper for the Opik REST API (no SDK / no API key required)."""
    headers = {"Accept": "application/json"}
    if workspace:
        headers["Comet-Workspace"] = workspace
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = _json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return _json.loads(raw) if raw else {}
    except HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"Opik API {method} {url} → HTTP {exc.code}: {body_text}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Opik at {url}: {exc.reason}") from exc


def _resolve_opik_project(dashboard_url: str, project_name: str) -> dict[str, str]:
    """Resolve workspace + project_id via the Opik REST API (no SDK / no API key)."""
    base_url = _opik_base_url(dashboard_url)
    workspace = _opik_workspace_from_url(dashboard_url) or "default"
    api_base = f"{base_url}/api/v1/private"

    # Find existing project by name
    projects_url = f"{api_base}/projects?page=1&size=100"
    result = _opik_rest("GET", projects_url, workspace=workspace)
    project_id = next(
        (p["id"] for p in result.get("content", []) if p.get("name") == project_name),
        None,
    )

    # Create the project if it doesn't exist yet
    if not project_id:
        _opik_rest("POST", f"{api_base}/projects", workspace=workspace, body={"name": project_name})
        result = _opik_rest("GET", projects_url, workspace=workspace)
        project_id = next(
            (p["id"] for p in result.get("content", []) if p.get("name") == project_name),
            None,
        )
    if not project_id:
        raise RuntimeError(f"Project '{project_name}' not found in workspace '{workspace}' after creation attempt.")

    return {
        "dashboard_url": base_url,
        "workspace_name": workspace,
        "project_id": project_id,
        "project_name": project_name,
    }


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value


def _repo_path_options(cfg: dict[str, Any]) -> list[str]:
    base_dir = ((cfg.get("repo_paths") or {}).get("base_dir") or "").strip()
    if not base_dir:
        return []
    root = Path(os.path.expandvars(base_dir)).expanduser()
    try:
        if not root.is_dir():
            return []
        options = [
            str(Path(entry.path).resolve())
            for entry in os.scandir(root)
            if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
        ]
    except OSError as exc:
        logger.warning("get_settings: could not list repo path base_dir=%s: %s", root, exc)
        return []
    return sorted(options)


@router.get("")
async def get_settings() -> dict[str, Any]:
    logger.debug("get_settings: loading config and runner choices")
    cfg = load_config()
    rc = runner_choices(cfg)
    logger.debug("get_settings: config loaded api.port=%s", cfg.get("api", {}).get("port"))
    return {
        **cfg,
        "runner_models": rc["models"],
        "runner_defaults": rc["defaults"],
        "repo_path_options": _repo_path_options(cfg),
    }


@router.put("")
async def put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("put_settings: received payload keys=%s", list(payload.keys()) if isinstance(payload, dict) else "non-dict")
    if not isinstance(payload, dict):
        logger.warning("put_settings: body is not a JSON object")
        raise HTTPException(400, "body must be a JSON object")
    merged = deepcopy(load_config())
    _merge_dict(merged, payload)
    errors = validate_config(merged)
    if errors:
        logger.warning("put_settings: validation failed: %s", errors)
        raise HTTPException(422, {"errors": errors})
    saved = save_config(payload)
    logger.info("put_settings: config saved successfully")
    return {"saved": True, "config": saved}


@router.post("/opik/connect")
async def connect_opik(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve workspace_name and project_id from a dashboard URL + project name,
    then persist the full Opik config and return it."""
    dashboard_url = (payload.get("dashboard_url") or "").strip()
    project_name = (payload.get("project_name") or "agent-runner").strip()
    if not dashboard_url:
        raise HTTPException(400, "dashboard_url is required")
    try:
        opik_settings = _resolve_opik_project(dashboard_url, project_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("connect_opik: failed to connect to Opik at %s: %s", dashboard_url, exc)
        raise HTTPException(502, f"Could not connect to Opik: {exc}") from exc
    save_config({"opik": opik_settings})
    logger.info("connect_opik: resolved and saved opik config workspace=%s project_id=%s",
                opik_settings["workspace_name"], opik_settings["project_id"])
    return opik_settings
