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
    """Extract batch blocks from YAML where batches/execution_schedule has duplicate batch keys."""
    match = re.search(r"^(?:batches|execution_schedule):\s*\n((?:[ \t]+.*\n?)*)", raw, re.MULTILINE)
    if not match:
        return []
    block = match.group(1)
    segments = re.split(r"(?=^[ \t]+(?:batch|batch_id):\s*\d)", block, flags=re.MULTILINE)
    batches: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.strip():
            continue
        try:
            parsed = yaml.safe_load(textwrap.dedent(segment))
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("extract_batches_from_duplicate_yaml: could not parse segment: %s", exc)
            continue
        if isinstance(parsed, dict) and ("batch" in parsed or "batch_id" in parsed):
            batches.append(parsed)
    return batches


def _normalize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Normalize a batch dict to use canonical 'batch_id' key."""
    if "batch_id" not in batch and "batch" in batch:
        batch["batch_id"] = batch.pop("batch")
    return batch


def parse_assignments_text(raw: str) -> dict[str, Any]:
    """Parse assignments content that may be strict JSON or legacy YAML.

    Normalises to the canonical shape: top-level ``batches`` list with
    ``batch_id`` keys on each batch dict, regardless of whether the input
    uses the legacy ``execution_schedule`` / ``batch`` shape.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("assignments artifact must parse to a mapping")

    # Normalise top-level key: legacy 'execution_schedule' → canonical 'batches'
    raw_batches = data.get("batches") or data.get("execution_schedule", [])
    if isinstance(raw_batches, dict):
        extracted = extract_batches_from_duplicate_yaml(raw)
        if extracted:
            raw_batches = extracted
        else:
            raw_batches = [raw_batches]
            index = 2
            while f"batch_{index}" in data:
                raw_batches.append(data[f"batch_{index}"])
                index += 1

    data["batches"] = [_normalize_batch(b) for b in raw_batches]
    data.pop("execution_schedule", None)
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
