"""Optional integration capability routes."""
from __future__ import annotations

import logging
import shutil
import subprocess

from fastapi import APIRouter

from ..config import load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _detect_azure_cli_extension() -> tuple[bool, bool]:
    az_path = shutil.which("az")
    if not az_path:
        return False, False
    try:
        result = subprocess.run(
            [az_path, "extension", "show", "--name", "azure-devops", "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("azure_devops_status: failed to inspect azure-devops extension: %s", exc)
        return True, False
    return True, result.returncode == 0


@router.get("/azure-devops/status")
async def azure_devops_status() -> dict[str, object]:
    cfg = (load_config().get("azure_devops") or {})
    cli_cfg = cfg.get("cli") or {}
    mcp_cfg = cfg.get("mcp") or {}
    write_back_enabled = bool(cfg.get("write_back_enabled"))

    cli_detected, cli_extension_installed = _detect_azure_cli_extension()
    cli_enabled = bool(cli_cfg.get("enabled"))
    mcp_enabled = bool(mcp_cfg.get("enabled"))
    mcp_server_url = (mcp_cfg.get("server_url") or "").strip()
    mcp_configured = bool(mcp_server_url)

    return {
        "manual": {
            "available": True,
            "status": "Always available",
        },
        "cli": {
            "detected": cli_detected,
            "extension_installed": cli_extension_installed,
            "enabled": cli_enabled,
            "can_read": cli_detected and cli_extension_installed and cli_enabled,
            "can_write": cli_detected and cli_extension_installed and cli_enabled and write_back_enabled,
        },
        "mcp": {
            "configured": mcp_configured,
            "reachable": False,
            "enabled": mcp_enabled,
            "server_url": mcp_server_url,
            "can_read": mcp_configured and mcp_enabled,
            "can_write": mcp_configured and mcp_enabled and write_back_enabled,
        },
        "write_back_enabled": write_back_enabled,
    }
