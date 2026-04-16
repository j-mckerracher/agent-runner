"""JSON Schemas for artifacts, tasks, and workflows.

Schemas are authored as Python dicts here so they can be introspected by
tests and emitted on demand. The canonical on-disk forms live alongside.
"""
from __future__ import annotations

from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent
