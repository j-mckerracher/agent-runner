from __future__ import annotations

import json
import logging
import re
import textwrap
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def extract_batches_from_duplicate_yaml(raw: str) -> list[dict[str, Any]]:
    """Extract batch blocks from YAML where execution_schedule has duplicate batch keys."""
    match = re.search(r"^execution_schedule:\s*\n((?:[ \t]+.*\n?)*)", raw, re.MULTILINE)
    if not match:
        return []
    execution_schedule_block = match.group(1)
    segments = re.split(r"(?=^[ \t]+batch:\s*\d)", execution_schedule_block, flags=re.MULTILINE)
    batches: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.strip():
            continue
        try:
            parsed = yaml.safe_load(textwrap.dedent(segment))
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("extract_batches_from_duplicate_yaml: could not parse segment: %s", exc)
            continue
        if isinstance(parsed, dict) and "batch" in parsed:
            batches.append(parsed)
    return batches


def parse_assignments_text(raw: str) -> dict[str, Any]:
    """Parse assignments content that may be strict JSON or legacy YAML."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("assignments artifact must parse to a mapping")

    execution_schedule = data.get("execution_schedule", [])
    if isinstance(execution_schedule, dict):
        extracted = extract_batches_from_duplicate_yaml(raw)
        if extracted:
            data["execution_schedule"] = extracted
        else:
            batches = [execution_schedule]
            index = 2
            while f"batch_{index}" in data:
                batches.append(data[f"batch_{index}"])
                index += 1
            data["execution_schedule"] = batches
    return data


def load_assignments_file(path: Path) -> dict[str, Any]:
    return parse_assignments_text(path.read_text(encoding="utf-8"))


def normalize_assignments_file(path: Path) -> bool:
    """Rewrite assignments artifacts to canonical JSON when parsing succeeds."""
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    data = parse_assignments_text(raw)
    normalized = json.dumps(data, indent=2, sort_keys=False) + "\n"
    if raw == normalized:
        return False
    path.write_text(normalized, encoding="utf-8")
    return True
