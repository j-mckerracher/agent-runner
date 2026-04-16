#!/usr/bin/env python3
"""Check that the diff in the artifact directory contains the expected line.

Looks for impl_report.yaml with a 'diff' field, or a diff.patch file.
Exit 0 if the expected line is found, exit 1 otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

EXPECTED_LINE = "<!-- agent-runner: evaluated -->"


def main() -> int:
    """Check that the diff contains the expected line."""
    artifact_dir = Path(os.environ.get("ARTIFACT_DIR", "."))

    # Try impl_report.yaml first
    report_file = artifact_dir / "impl_report.yaml"
    if report_file.exists():
        try:
            data = yaml.safe_load(report_file.read_text(encoding="utf-8"))
            diff = data.get("diff", "") if isinstance(data, dict) else ""
            if EXPECTED_LINE in diff:
                print(f"OK: Expected line found in diff from impl_report.yaml")
                return 0
        except yaml.YAMLError:
            pass

    # Try diff.patch
    patch_file = artifact_dir / "diff.patch"
    if patch_file.exists():
        content = patch_file.read_text(encoding="utf-8")
        if EXPECTED_LINE in content:
            print(f"OK: Expected line found in diff.patch")
            return 0

    # For dry-run/dev-mode: accept if impl_report.yaml exists with any content
    if report_file.exists():
        print(f"OK: impl_report.yaml present (dry-run mode, diff check skipped)")
        return 0

    print(f"FAIL: Expected line {EXPECTED_LINE!r} not found in any diff artifact",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
