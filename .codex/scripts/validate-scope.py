#!/usr/bin/env python3
"""Validate file paths against forbidden patterns and allowed directory boundaries.

Usage:
    validate-scope.py [--artifact-root <path>] <file_path> [file_path...]
    echo -e "path1\\npath2" | validate-scope.py [--artifact-root <path>]

Exit codes: 0 = all paths clean, 1 = violations found, 2 = usage error
"""

from __future__ import annotations

import json
import os
import sys

# ---------------------------------------------------------------------------
# Forbidden-pattern definitions
# ---------------------------------------------------------------------------

FORBIDDEN_BASENAME_SUBSTRINGS = (
    ("secret", "Matches forbidden pattern: *secret*"),
    ("credential", "Matches forbidden pattern: *credential*"),
    ("password", "Matches forbidden pattern: *password*"),
)

FORBIDDEN_BASENAME_EXACT = {
    "package-lock.json": "Matches forbidden pattern: package-lock.json",
    "yarn.lock": "Matches forbidden pattern: yarn.lock",
}

FORBIDDEN_PATH_SEGMENTS = (
    ("node_modules/", "Path contains forbidden segment: node_modules/"),
    ("dist/", "Path contains forbidden segment: dist/"),
    ("build/", "Path contains forbidden segment: build/"),
    (".git/", "Path contains forbidden segment: .git/"),
)


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

def check_file(filepath: str, artifact_root: str) -> tuple[str, str] | None:
    """Return a (file, reason) tuple if the path violates a rule, else None."""
    lower = filepath.lower()
    basename = os.path.basename(filepath).lower()

    # .env files: basename starts with ".env" or contains ".env"
    if ".env" in basename:
        return (filepath, "Matches forbidden pattern: *.env*")

    # Forbidden substrings in basename
    for substring, reason in FORBIDDEN_BASENAME_SUBSTRINGS:
        if substring in basename:
            return (filepath, reason)

    # Exact basename matches
    if basename in FORBIDDEN_BASENAME_EXACT:
        return (filepath, FORBIDDEN_BASENAME_EXACT[basename])

    # Forbidden path segments
    for segment, reason in FORBIDDEN_PATH_SEGMENTS:
        if segment in lower:
            return (filepath, reason)

    # Artifact-root boundary check
    if artifact_root:
        normalised = filepath.rstrip("/")
        if normalised != artifact_root and not normalised.startswith(artifact_root + "/"):
            return (filepath, f"Path is outside artifact root: {artifact_root}")

    return None


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

USAGE = """\
Usage: validate-scope.py [--artifact-root <path>] <file_path> [file_path...]
       echo -e "path1\\npath2" | validate-scope.py [--artifact-root <path>]

Options:
  --artifact-root <path>  Also verify every file resides under this root.

Exit codes:
  0  All paths clean
  1  Violations found
  2  Usage error"""


def die(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.write(f"{USAGE}\n")
    sys.exit(2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    artifact_root = ""
    files: list[str] = []

    # ---- parse args --------------------------------------------------------
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            die("")
        elif arg == "--artifact-root":
            if i + 1 >= len(args):
                die("Error: --artifact-root requires a value")
            artifact_root = args[i + 1]
            i += 2
        elif arg == "--":
            files.extend(args[i + 1 :])
            break
        elif arg.startswith("-"):
            die(f"Error: Unknown option: {arg}")
        else:
            files.append(arg)
            i += 1
            continue
        continue

    # ---- read from stdin when no positional args and stdin is piped ---------
    if not files and not sys.stdin.isatty():
        for line in sys.stdin:
            stripped = line.rstrip("\n\r")
            if stripped:
                files.append(stripped)

    if not files:
        die("Error: no file paths provided")

    if artifact_root:
        artifact_root = artifact_root.rstrip("/")

    # ---- check each file ---------------------------------------------------
    violations: list[dict[str, str]] = []
    for filepath in files:
        result = check_file(filepath, artifact_root)
        if result is not None:
            violations.append({"file": result[0], "reason": result[1]})

    status = "fail" if violations else "pass"

    output = {
        "status": status,
        "files_checked": len(files),
        "violations": violations,
    }
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")

    return 1 if status == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
