"""Strict YAML I/O for eval framework — raises on missing/malformed files."""

from pathlib import Path
from typing import Any

import yaml


class YamlError(Exception):
    """Raised when YAML file is missing or cannot be parsed."""


def dump_yaml(data: dict[str, Any], path: Path) -> None:
    """Serialize a dict to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from disk. Raises YamlError on missing file or parse failure."""
    if not path.is_file():
        raise YamlError(f"YAML file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise YamlError(f"YAML parse error in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise YamlError(f"Expected a YAML mapping in {path}, got {type(payload).__name__}")

    return payload
