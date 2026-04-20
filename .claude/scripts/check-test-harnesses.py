#!/usr/bin/env python3
"""Check that every Angular component file has an adjacent test-harness file.

Accepts file paths as positional arguments or newline-delimited paths on
stdin.  Filters to ``*.component.ts`` (excluding ``.spec.ts``, ``.html``,
``.scss`` variants) and verifies the corresponding
``*.test-harness.ts`` file exists on disk.

Exit codes:
    0 – all components have a test harness (pass)
    1 – one or more harnesses are missing (fail)
    2 – usage error (no paths supplied)
"""

from __future__ import annotations

import json
import os
import sys


def _usage() -> int:
    name = os.path.basename(sys.argv[0])
    sys.stderr.write(f"Usage: {name} <file_path> [file_path...]\n")
    sys.stderr.write(f'       echo "path1" | {name}\n')
    return 2


def _is_component_ts(path: str) -> bool:
    """Return *True* only for ``*.component.ts`` that aren't spec/html/scss."""
    if not path.endswith(".component.ts"):
        return False
    if path.endswith(".component.spec.ts"):
        return False
    if path.endswith(".component.html"):
        return False
    if path.endswith(".component.scss"):
        return False
    return True


def main() -> int:
    # -- collect paths from args or stdin --------------------------------
    paths: list[str] = []

    if len(sys.argv) > 1:
        paths = sys.argv[1:]
    elif not sys.stdin.isatty():
        for line in sys.stdin:
            stripped = line.strip()
            if stripped:
                paths.append(stripped)
    else:
        return _usage()

    if not paths:
        return _usage()

    # -- filter to component files ---------------------------------------
    components = [p for p in paths if _is_component_ts(p)]

    # -- check for adjacent test-harness files ---------------------------
    missing: list[dict[str, str]] = []
    for comp in components:
        harness = comp.removesuffix(".component.ts") + ".test-harness.ts"
        if not os.path.isfile(harness):
            missing.append({"component": comp, "expected_harness": harness})

    # -- produce JSON output ---------------------------------------------
    status = "fail" if missing else "pass"
    result = {
        "status": status,
        "components_checked": len(components),
        "missing_harnesses": missing,
    }
    print(json.dumps(result, indent=2))

    return 1 if status == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
