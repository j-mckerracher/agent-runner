#!/usr/bin/env python3
"""Interactive entrypoint for the agent workflow runner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT / "packages" / "runner", ROOT / "packages" / "shared",
                  ROOT / "packages" / "registry", ROOT / "packages" / "harness"):
    candidate_str = str(candidate)
    if candidate.is_dir() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from agent_runner.cli.interactive import main


if __name__ == "__main__":
    raise SystemExit(main())
