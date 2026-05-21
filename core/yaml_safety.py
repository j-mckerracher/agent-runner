"""Safe YAML loading with structured error reporting for agent-generated artifacts."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SafeYamlResult:
    """Result of a safe YAML load operation.

    Attributes:
        data: The parsed YAML dict, or None if loading failed.
        errors: Fatal problems that prevented loading (parse errors, missing files).
        warnings: Non-fatal issues (e.g. empty file, unexpected type).
    """

    data: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0 and self.data is not None


def safe_load_yaml_file(path: Path) -> SafeYamlResult:
    """Load a YAML file with full error handling.

    Returns SafeYamlResult with data on success, or errors list on failure.
    Never raises — all failures are captured in the result.
    """
    if not path.is_file():
        return SafeYamlResult(errors=[f"File not found: {path}"])

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        msg = f"YAML parse error in {path}: {exc}"
        logger.warning(msg)
        return SafeYamlResult(errors=[msg])
    except OSError as exc:
        msg = f"Cannot read {path}: {exc}"
        logger.warning(msg)
        return SafeYamlResult(errors=[msg])

    if payload is None:
        return SafeYamlResult(data={}, warnings=[f"Empty YAML file: {path}"])

    if not isinstance(payload, dict):
        msg = f"Expected a YAML mapping in {path}, got {type(payload).__name__}"
        logger.warning(msg)
        return SafeYamlResult(errors=[msg])

    return SafeYamlResult(data=payload)


def load_yaml_mapping_safe(path: Path) -> dict[str, Any]:
    """Convenience wrapper — returns {} on any error.

    This is the drop-in replacement for the old _load_yaml_mapping.
    Use safe_load_yaml_file() directly when you need structured error details.
    """
    result = safe_load_yaml_file(path)
    return result.data if result.is_valid else {}


def validate_yaml_artifact(
    path: Path,
    *,
    required_fields: list[str] | None = None,
    field_types: dict[str, type] | None = None,
) -> SafeYamlResult:
    """Load and structurally validate a YAML artifact.

    Checks that the file is valid YAML and contains required fields
    with expected types. Returns SafeYamlResult with accumulated errors.
    """
    result = safe_load_yaml_file(path)
    if not result.is_valid:
        return result

    data = result.data
    assert data is not None  # safe_load_yaml_file guarantees data is dict when is_valid

    if required_fields:
        for field in required_fields:
            if field not in data:
                result.errors.append(f"Missing required field '{field}' in {path}")

    if field_types:
        for field, expected_type in field_types.items():
            if field in data and not isinstance(data[field], expected_type):
                result.errors.append(
                    f"Field '{field}' in {path} expected {expected_type.__name__}, "
                    f"got {type(data[field]).__name__}"
                )

    return result
