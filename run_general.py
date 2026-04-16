#!/usr/bin/env python3
"""General-purpose agent execution entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

from agent_runner.cli.general import main


if __name__ == "__main__":
    raise SystemExit(main())
