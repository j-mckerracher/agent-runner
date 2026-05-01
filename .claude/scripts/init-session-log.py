#!/usr/bin/env python3
"""Create a structured session-log JSON file for an agent.

Drop-in replacement for init-session-log.sh that removes the jq
dependency by using Python's built-in ``json`` module instead.

Exit codes:
    0 – success (file path printed to stdout)
    1 – runtime error (I/O failure, etc.)
    2 – usage / validation error
"""

import json
import os
import sys
from datetime import datetime, timezone

VALID_AGENTS = [
    "workflow_runner",
    "orchestrator",
    "intake",
    "reference_librarian",
    "task_generator",
    "assignment",
    "software_engineer",
    "qa",
    "information_explorer",
    "lessons_optimizer",
]

USAGE = f"""\
Usage: {os.path.basename(__file__)} <artifact_root> <change_id> <agent_name> <identifier> [iteration]

Creates a structured session log JSON file for an agent.

Arguments:
  artifact_root  Root path for artifacts
  change_id      Change identifier (e.g., WI-12345)
  agent_name     One of: {' '.join(VALID_AGENTS)}
  identifier     Log type suffix (e.g., session, query, exploration, state_transition)
  iteration      Optional, defaults to 1

Output:
  Prints the created file path to stdout.\
"""


def main() -> int:
    args = sys.argv[1:]

    if len(args) < 4 or len(args) > 5:
        print(USAGE, file=sys.stderr)
        return 2

    artifact_root = args[0]
    change_id = args[1]
    agent_name = args[2]
    identifier = args[3]
    iteration_raw = args[4] if len(args) == 5 else "1"

    # --- validate agent_name ---
    if agent_name not in VALID_AGENTS:
        print(f"Error: Invalid agent_name '{agent_name}'.", file=sys.stderr)
        print(f"Must be one of: {' '.join(VALID_AGENTS)}", file=sys.stderr)
        return 2

    # --- validate iteration ---
    if not iteration_raw.isdigit() or int(iteration_raw) < 1:
        print(
            f"Error: iteration must be a positive integer, got '{iteration_raw}'.",
            file=sys.stderr,
        )
        return 2

    iteration = int(iteration_raw)

    # --- timestamps ---
    now = datetime.now(timezone.utc)
    ts_file = now.strftime("%Y%m%d_%H%M%S")
    ts_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- paths ---
    log_dir = os.path.join(artifact_root, change_id, "logs", agent_name)
    log_file = os.path.join(log_dir, f"{ts_file}_{identifier}.json")

    # --- create directory ---
    try:
        os.makedirs(log_dir, exist_ok=True)
    except OSError as exc:
        print(f"Error: Failed to create log directory '{log_dir}': {exc}", file=sys.stderr)
        return 1

    # --- build payload ---
    payload = {
        "log_type": agent_name,
        "timestamp": ts_iso,
        "change_id": change_id,
        "iteration": iteration,
        "session_summary": {
            "input_artifacts_read": [],
            "output_artifacts_written": [],
        },
        "decisions_made": [],
        "issues_encountered": [],
        "notes": "",
    }

    # --- write file ---
    try:
        with open(log_file, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        print(f"Error: Failed to write log file '{log_file}': {exc}", file=sys.stderr)
        return 1

    print(log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
