#!/usr/bin/env python3
"""CLI helper: request user input during an agent workflow.

Usage by an agent (simple mode):

    python agent-script-source/request-user-input.py \\
      --change-id "$CHANGE_ID" \\
      --stage execution \\
      --agent software-engineer-hyperagent \\
      --uow-id UOW-001 \\
      --title "Need compatibility decision" \\
      --message "The requested change alters an existing API contract..." \\
      --question "Should old behavior be preserved?"

Usage by an agent (request-file mode — safer for long messages):

    python agent-script-source/request-user-input.py \\
      --request-file /path/to/request.json

The command blocks until the user responds through the GUI (or TTY) and then
prints response JSON to stdout.  The parent agent should read the JSON and
continue the task.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure the runner root is on sys.path so core/ imports work.
_RUNNER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RUNNER_ROOT not in sys.path:
    sys.path.insert(0, _RUNNER_ROOT)

from core.user_escalation import request_user_input  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Request user input during an agent workflow.",
    )
    # --- Simple CLI args ---
    p.add_argument("--change-id", default=os.environ.get("CHANGE_ID", ""))
    p.add_argument("--stage", default=os.environ.get("AGENT_RUNNER_CURRENT_STAGE", ""))
    p.add_argument("--agent", default="")
    p.add_argument("--uow-id", default=None)
    p.add_argument("--title", default="")
    p.add_argument("--message", default="")
    p.add_argument("--message-file", default=None, help="Read message body from a file")
    p.add_argument(
        "--question", action="append", dest="questions", default=[],
        help="A question string (repeatable).",
    )
    p.add_argument("--severity", default="blocking", choices=["blocking", "approval", "clarification"])
    p.add_argument("--conversation-id", default=None)
    p.add_argument("--resolution-criteria", default=None)
    p.add_argument("--timeout", type=int, default=None, help="Timeout in seconds (default: no timeout)")

    # --- Request file mode ---
    p.add_argument(
        "--request-file", default=None,
        help="Path to a JSON file containing the full request payload. "
             "Overrides all other arguments.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.request_file:
        with open(args.request_file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        # Allow request file to omit change_id if env is set.
        payload.setdefault("change_id", os.environ.get("CHANGE_ID", ""))
        payload.setdefault("stage", os.environ.get("AGENT_RUNNER_CURRENT_STAGE", ""))
    else:
        message = args.message
        if args.message_file:
            with open(args.message_file, "r", encoding="utf-8") as fh:
                message = fh.read()
        if not args.questions:
            print("ERROR: at least one --question is required (or use --request-file)", file=sys.stderr)
            sys.exit(1)
        payload = {
            "change_id": args.change_id,
            "stage": args.stage,
            "agent": args.agent,
            "title": args.title,
            "message": message,
            "questions": args.questions,  # list of strings; will be normalised
            "severity": args.severity,
            "conversation_id": args.conversation_id,
            "uow_id": args.uow_id,
            "resolution_criteria": args.resolution_criteria,
            "timeout_seconds": args.timeout,
        }

    # Remove None values so they become keyword defaults.
    payload = {k: v for k, v in payload.items() if v is not None}

    if not payload.get("change_id"):
        print("ERROR: --change-id is required (or set CHANGE_ID env var)", file=sys.stderr)
        sys.exit(1)

    try:
        response = request_user_input(**payload)
    except TimeoutError as exc:
        print(json.dumps({"error": "timeout", "detail": str(exc)}))
        sys.exit(2)
    except RuntimeError as exc:
        print(json.dumps({"error": "no_interactive_channel", "detail": str(exc)}))
        sys.exit(3)

    # Print response JSON to stdout for the calling agent.
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()

