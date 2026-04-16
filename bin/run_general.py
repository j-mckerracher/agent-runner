#!/usr/bin/env python3
"""General-purpose agent execution entrypoint (shim)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for candidate in (ROOT / "packages" / "runner", ROOT / "packages" / "shared",
                  ROOT / "packages" / "registry", ROOT / "packages" / "harness"):
    candidate_str = str(candidate)
    if candidate.is_dir() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from agent_runner.cli.general import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
