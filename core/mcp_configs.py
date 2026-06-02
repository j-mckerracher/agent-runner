"""Utilities for generating and registering the escalation MCP server config.

Per-runner strategy:
  claude   — write a temp JSON file; pass via --mcp-config <path>
  gemini   — idempotently add to ~/.gemini/settings.json mcpServers; pass
             --allowed-mcp-server-names agent-workbench-escalation per run
  codex    — idempotently add via `codex mcp add`; automatically available in all runs
  openai-  — native function-tool in _OpenaiCompatToolRuntime (no MCP needed)
  compat
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "agent-workbench-escalation"
_RUNNER_ROOT = Path(__file__).resolve().parent.parent
_GEMINI_SETTINGS = Path.home() / ".gemini" / "settings.json"


def _runner_python() -> str:
    """Return the Python executable used to run the MCP server."""
    venv_python = _RUNNER_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _mcp_server_entry(change_id: str) -> dict:
    """Build the MCP server entry dict for the config JSON."""
    return {
        "command": _runner_python(),
        "args": ["-m", "core.escalation_mcp_server"],
        "env": {
            "AGENT_RUNNER_CHANGE_ID": change_id,
            "AGENT_RUNNER_ROOT": str(_RUNNER_ROOT),
            "AGENT_RUNNER_DATA_DIR": os.environ.get("AGENT_RUNNER_DATA_DIR", ""),
            "AGENT_RUNNER_CURRENT_STAGE": os.environ.get("AGENT_RUNNER_CURRENT_STAGE", ""),
            "PYTHONPATH": str(_RUNNER_ROOT),
        },
        "type": "stdio",
    }


def write_claude_mcp_config(change_id: str) -> Path:
    """Write a temp MCP config JSON for the Claude CLI --mcp-config flag.

    Returns the path to the temp file. The caller is responsible for
    cleanup after the CLI process exits (or let the OS clean /tmp on reboot).
    """
    config = {
        "mcpServers": {
            MCP_SERVER_NAME: _mcp_server_entry(change_id),
        }
    }
    fd, path = tempfile.mkstemp(
        prefix=f"agent-wb-mcp-{change_id}-",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    logger.debug("write_claude_mcp_config: wrote %s for change_id=%s", path, change_id)
    return Path(path)


def ensure_gemini_mcp_registered() -> bool:
    """Idempotently add the escalation server to ~/.gemini/settings.json.

    Returns True if the server was newly registered, False if it was already
    present (no-op). Raises on IO errors.
    """
    if not _GEMINI_SETTINGS.exists():
        logger.warning(
            "ensure_gemini_mcp_registered: %s not found; skipping Gemini MCP registration",
            _GEMINI_SETTINGS,
        )
        return False

    with open(_GEMINI_SETTINGS, "r", encoding="utf-8") as fh:
        settings: dict = json.load(fh)

    mcp_servers: dict = settings.setdefault("mcpServers", {})
    if MCP_SERVER_NAME in mcp_servers:
        logger.debug("ensure_gemini_mcp_registered: %s already registered", MCP_SERVER_NAME)
        return False

    # Register without change_id — at Gemini spawn time the env vars are
    # inherited from the runner process which already has AGENT_RUNNER_CHANGE_ID set.
    entry = {
        "command": _runner_python(),
        "args": ["-m", "core.escalation_mcp_server"],
        "env": {
            "AGENT_RUNNER_ROOT": str(_RUNNER_ROOT),
            "PYTHONPATH": str(_RUNNER_ROOT),
        },
        "type": "stdio",
    }
    mcp_servers[MCP_SERVER_NAME] = entry
    settings["mcpServers"] = mcp_servers

    tmp_path = _GEMINI_SETTINGS.with_suffix(".json.mcp-tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        tmp_path.replace(_GEMINI_SETTINGS)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    logger.info("ensure_gemini_mcp_registered: registered %s in %s", MCP_SERVER_NAME, _GEMINI_SETTINGS)
    return True


def ensure_codex_mcp_registered() -> bool:
    """Idempotently register the escalation server with the Codex CLI.

    Uses `codex mcp get` to probe for existing registration, then
    `codex mcp add` to register if absent. The server is then available
    to all subsequent `codex exec` invocations.

    Returns True if newly registered, False if already present (no-op).
    Raises on unexpected errors.
    """
    if not _is_codex_available():
        logger.debug("ensure_codex_mcp_registered: codex CLI not found; skipping")
        return False

    python_bin = _runner_python()
    probe = subprocess.run(
        ["codex", "mcp", "get", MCP_SERVER_NAME],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        if _codex_registration_matches(probe.stdout, python_bin):
            logger.debug("ensure_codex_mcp_registered: %s already registered", MCP_SERVER_NAME)
            return False
        remove = subprocess.run(
            ["codex", "mcp", "remove", MCP_SERVER_NAME],
            capture_output=True,
            text=True,
        )
        if remove.returncode != 0:
            raise RuntimeError(
                f"ensure_codex_mcp_registered: `codex mcp remove` failed "
                f"(exit {remove.returncode}): {remove.stderr.strip()}"
            )

    cmd = [
        "codex", "mcp", "add",
        MCP_SERVER_NAME,
        "--env", f"AGENT_RUNNER_ROOT={_RUNNER_ROOT}",
        "--env", f"PYTHONPATH={_RUNNER_ROOT}",
        "--",
        python_bin, "-m", "core.escalation_mcp_server",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ensure_codex_mcp_registered: `codex mcp add` failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    logger.info("ensure_codex_mcp_registered: registered %s with Codex CLI", MCP_SERVER_NAME)
    return True


def _codex_registration_matches(output: str, python_bin: str) -> bool:
    return (
        f"command: {python_bin}" in (output or "")
        and "args: -m core.escalation_mcp_server" in (output or "")
    )


def _is_codex_available() -> bool:
    import shutil
    return shutil.which("codex") is not None
