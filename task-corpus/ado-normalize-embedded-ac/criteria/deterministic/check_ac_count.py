#!/usr/bin/env python3
"""Check that story.yaml in ARTIFACT_DIR contains at least 2 acceptance criteria.

Exit 0 if >= 2 ACs found, exit 1 otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml


def main() -> int:
    """Check acceptance criteria count in story.yaml."""
    artifact_dir = Path(os.environ.get("ARTIFACT_DIR", "."))
    story_file = artifact_dir / "story.yaml"

    if not story_file.exists():
        print(f"ERROR: story.yaml not found in {artifact_dir}", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(story_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"ERROR: Failed to parse story.yaml: {exc}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print("ERROR: story.yaml must be a YAML mapping", file=sys.stderr)
        return 1

    # Look for acceptance_criteria key (list or dict)
    acs = data.get("acceptance_criteria", data.get("acceptanceCriteria", []))
    if isinstance(acs, dict):
        # Could be keyed by deterministic/rubric
        all_acs = []
        for v in acs.values():
            if isinstance(v, list):
                all_acs.extend(v)
        acs = all_acs

    count = len(acs) if isinstance(acs, list) else 0
    if count >= 2:
        print(f"OK: {count} acceptance criteria found in story.yaml")
        return 0
    else:
        print(f"FAIL: Only {count} acceptance criteria found (need >= 2)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
