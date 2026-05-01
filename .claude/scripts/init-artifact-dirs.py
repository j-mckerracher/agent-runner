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

    dirs = [
        os.path.join(base, "intake"),
        os.path.join(base, "planning"),
        os.path.join(base, "execution"),
        os.path.join(base, "qa", "evidence", "screenshots"),
        os.path.join(base, "qa", "evidence", "test_output"),
        os.path.join(base, "qa", "evidence", "logs"),
        os.path.join(base, "summary"),
        os.path.join(base, "status", "escalated_archive"),
        os.path.join(base, "logs", "workflow_runner"),
        os.path.join(base, "logs", "intake"),
        os.path.join(base, "logs", "reference_librarian"),
        os.path.join(base, "logs", "task_generator"),
        os.path.join(base, "logs", "assignment"),
        os.path.join(base, "logs", "software_engineer"),
        os.path.join(base, "logs", "qa"),
        os.path.join(base, "logs", "information_explorer"),
        os.path.join(base, "logs", "lessons_optimizer"),
    ]

    for d in dirs:
        os.makedirs(d, exist_ok=True)

    print(
        json.dumps(
            {
                "status": "ok",
                "artifact_root": artifact_root,
                "change_id": change_id,
                "directories_created": len(dirs),
            }
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
