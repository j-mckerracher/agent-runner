"""Repository-root conftest.py.

Adds all four package source directories to sys.path so that tests under
``tests/`` can import from all four packages without requiring PYTHONPATH
to be set explicitly. This mirrors the ``pythonpath`` setting in
``pyproject.toml`` (which applies to pytest's configured testpaths) and
ensures plain ``pytest`` invocations from the repo root also work.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

for _pkg in ("shared", "runner", "registry", "harness"):
    _pkg_path = str(_REPO_ROOT / "packages" / _pkg)
    if _pkg_path not in sys.path:
        sys.path.insert(0, _pkg_path)
˚