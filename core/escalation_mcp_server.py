"""MCP server that exposes the workbench escalation channel as a structured tool.

Serves over stdio so any MCP-capable CLI runner can spawn it as a subprocess.

Run directly:
    python -m core.escalation_mcp_server

Registered for Claude via --mcp-config <path>.
Registered for Gemini via core.mcp_configs.ensure_gemini_mcp_registered().
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the runner root is on sys.path so core/ imports work regardless of
# the working directory the spawning CLI uses.
_RUNNER_ROOT = Path(__file__).resolve().parent.parent
if str(_RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_RUNNER_ROOT))

from fastmcp import FastMCP  # noqa: E402
from core.user_escalation import request_user_input as _request_user_input  # noqa: E402

mcp = FastMCP(
    name="agent-workbench-escalation",
    instructions=(
        "Provides the `request_user_input` tool for agent-workbench workflow stages. "
        "Call this tool to pause the current turn and ask the human user a blocking "
        "or clarification question. The call blocks until the user replies via the "
        "workbench GUI or TTY, then returns their response as JSON."
    ),
)


@mcp.tool()
def request_user_input(
    title: str,
    message: str,
    questions: list[str],
    severity: str = "clarification",
    conversation_id: str | None = None,
    resolution_criteria: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    """Pause the agent's turn and ask the user for input via the workbench escalation channel.

    Blocks until the user replies through the GUI or TTY. Returns the response dict.

    Args:
        title: Short title displayed on the escalation card (e.g. "Missing AC for edge case").
        message: Full context, recommended default, and impact — shown to the user before the question.
        questions: Exactly one decision-forcing question per call.
        severity: "clarification" (default), "blocking", or "approval".
        conversation_id: Reuse an existing conversation thread for multi-turn dialogue.
        resolution_criteria: Optional criteria the user's answer must satisfy.
        timeout_seconds: How long to wait before giving up. None means wait indefinitely.
    """
    change_id = os.environ.get("AGENT_RUNNER_CHANGE_ID", "")
    stage = os.environ.get("AGENT_RUNNER_CURRENT_STAGE", "")
    agent = os.environ.get("AGENT_RUNNER_CURRENT_AGENT", "")

    if not change_id:
        raise ValueError(
            "AGENT_RUNNER_CHANGE_ID environment variable is not set. "
            "The workbench runner must set this before spawning the MCP server."
        )

    kwargs: dict = dict(
        change_id=change_id,
        stage=stage,
        agent=agent,
        title=title,
        message=message,
        questions=questions,
        severity=severity,
    )
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id
    if resolution_criteria is not None:
        kwargs["resolution_criteria"] = resolution_criteria
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds

    return _request_user_input(**kwargs)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False, log_level="ERROR")
