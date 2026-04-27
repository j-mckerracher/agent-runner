#!/usr/bin/env python3
"""Validate unified diff size and categorize by change threshold.

Reads a unified diff from a file (--file <path>) or stdin, counts added and
removed lines (skipping +++ / --- header lines), and emits a JSON report with
a size category:

  <100  changed lines → proceed
  ≤300  changed lines → warn
  ≤500  changed lines → require-approval
  >500  changed lines → kill

Exit codes: 0 when category is "proceed", 1 otherwise.
"""

import argparse
import json
import os
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a unified diff and categorize by size threshold.",
        usage="%(prog)s [--file <diff_file>]",
    )
    parser.add_argument(
        "--file",
        dest="diff_file",
        default=None,
        help="Path to a unified diff file. Reads from stdin when omitted.",
    )
    return parser.parse_args(argv)


def _read_diff(path: str | None) -> str:
    """Return the raw diff content from *path* or stdin."""
    if path is not None:
        if not os.path.isfile(path):
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(2)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    if sys.stdin.isatty():
        print(
            "Usage: validate-diff-size.py [--file <diff_file>]\n"
            "  Reads a unified diff and categorizes by size threshold.\n"
            "  If no --file is given, reads diff from stdin.",
            file=sys.stderr,
        )
        sys.exit(2)

    return sys.stdin.read()


def _categorize(changed_lines: int) -> tuple[str, str]:
    """Return (category, action) for the given number of changed lines."""
    if changed_lines < 100:
        return "proceed", "Proceed normally"
    if changed_lines <= 300:
        return "warn", "Warn, request justification"
    if changed_lines <= 500:
        return "require-approval", "Require explicit approval"
    return "kill", "Kill switch: scope reduction required"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    content = _read_diff(args.diff_file)

    total_diff_lines = 0
    added_lines = 0
    removed_lines = 0

    for line in content.splitlines():
        total_diff_lines += 1
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added_lines += 1
        elif line.startswith("-"):
            removed_lines += 1

    changed_lines = added_lines + removed_lines
    category, action = _categorize(changed_lines)

    report = {
        "total_diff_lines": total_diff_lines,
        "changed_lines": changed_lines,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "category": category,
        "action": action,
    }
    print(json.dumps(report, indent=2))

    return 0 if category == "proceed" else 1


if __name__ == "__main__":
    sys.exit(main())
