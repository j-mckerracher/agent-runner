#!/usr/bin/env python3
"""Detect UI-related file changes from a list of file paths.

Usage:
    detect-ui-changes.py <file_path> [file_path ...]
    echo "path1" | detect-ui-changes.py

Accepts file paths as positional arguments or via stdin (one per line).
Outputs a JSON report indicating whether any UI changes were detected.

Exit codes: 0 = success, 2 = usage error (no paths provided)
"""

from __future__ import annotations

import json
import re
import sys
from typing import List

# UI file-extension patterns (matched against the filename/full path)
FILE_PATTERNS: List[str] = [
    r"\.html$",
    r"\.css$",
    r"\.scss$",
    r"\.tsx$",
    r"\.vue$",
    r"\.component\.ts$",
    r"\.component\.html$",
    r"\.styles\.ts$",
]

# UI directory patterns (matched against the full path)
DIR_PATTERNS: List[str] = [
    r"components/",
    r"ui/",
    r"styles/",
    r"views/",
    r"templates/",
]

_COMPILED_FILE = [re.compile(p) for p in FILE_PATTERNS]
_COMPILED_DIR = [re.compile(p) for p in DIR_PATTERNS]


def _collect_paths() -> List[str]:
    """Gather file paths from CLI args or stdin."""
    if len(sys.argv) > 1:
        return sys.argv[1:]

    if not sys.stdin.isatty():
        return [line for line in (l.strip() for l in sys.stdin) if line]

    return []


def detect(paths: List[str]) -> dict:
    """Check each path against file and directory patterns.

    Returns a JSON-serialisable report dict.
    """
    ui_detected = False
    matched_files: List[dict] = []

    for filepath in paths:
        matched = False

        for pattern, compiled in zip(FILE_PATTERNS, _COMPILED_FILE):
            if compiled.search(filepath):
                matched_files.append(
                    {"file": filepath, "matched_pattern": pattern}
                )
                ui_detected = True
                matched = True
                break

        if matched:
            continue

        for pattern, compiled in zip(DIR_PATTERNS, _COMPILED_DIR):
            if compiled.search(filepath):
                matched_files.append(
                    {"file": filepath, "matched_pattern": pattern}
                )
                ui_detected = True
                break

    return {
        "ui_changes_detected": ui_detected,
        "files_checked": len(paths),
        "matched_files": matched_files,
    }


def main() -> int:
    paths = _collect_paths()
    if not paths:
        prog = sys.argv[0].rsplit("/", 1)[-1]
        print(f'Usage: {prog} <file_path> [file_path...]')
        print(f'       echo "path1" | {prog}')
        return 2

    report = detect(paths)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
