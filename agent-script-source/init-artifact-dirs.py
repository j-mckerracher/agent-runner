#!/usr/bin/env python3
"""Initialise the artifact directory tree for a given CHANGE-ID.

Usage:
    init-artifact-dirs.py <artifact_root> <CHANGE-ID>

Creates the standard set of artifact directories and prints a JSON
summary to stdout.  Exit codes: 0 = success, 2 = usage error.
"""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"Usage: {os.path.basename(sys.argv[0])} <artifact_root> <CHANGE-ID>",
            file=sys.stderr,
        )
        return 2

    artifact_root = sys.argv[1]
    change_id = sys.argv[2]
    base = os.path.join(artifact_root, change_id)
    artifact_root_path = Path(artifact_root).expanduser().resolve()
    if artifact_root_path.name == "logs":
        logs_root = artifact_root_path
    elif artifact_root_path.name == "agent-context":
        logs_root = artifact_root_path.parent / "logs"
    else:
        logs_root = artifact_root_path / "logs"
    # Log dirs are logs/{agent_name}/ — no change_id subdirectory.
    # The change_id is recorded inside each JSON log file.

    artifact_dirs = [
        os.path.join(base, "intake"),
        os.path.join(base, "planning"),
        os.path.join(base, "execution"),
        os.path.join(base, "qa", "evidence", "screenshots"),
        os.path.join(base, "qa", "evidence", "test_output"),
        os.path.join(base, "qa", "evidence", "logs"),
        os.path.join(base, "summary"),
    ]

    log_dirs = [
        logs_root / "orchestrator",
        logs_root / "intake",
        logs_root / "reference_librarian",
        logs_root / "task_generator",
        logs_root / "assignment",
        logs_root / "task_plan_evaluator",
        logs_root / "assignment_evaluator",
        logs_root / "software_engineer",
        logs_root / "implementation_evaluator",
        logs_root / "qa",
        logs_root / "qa_evaluator",
        logs_root / "information_explorer",
    ]

    for d in artifact_dirs:
        os.makedirs(d, exist_ok=True)
    for d in log_dirs:
        d.mkdir(parents=True, exist_ok=True)

    print(
        json.dumps(
            {
                "status": "ok",
                "artifact_root": artifact_root,
                "logs_root": str(logs_root),
                "change_id": change_id,
                "directories_created": len(artifact_dirs) + len(log_dirs),
            }
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
