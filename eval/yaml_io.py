"""Small YAML load/dump helpers with actionable validation errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Union, cast

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised only in broken envs
    raise RuntimeError("PyYAML is required. Install with: python3 -m pip install -r requirements.txt") from exc

PathLike = Union[str, Path]


class YamlError(ValueError):
    """Raised when a YAML file cannot be loaded or has the wrong top-level type."""


def load_yaml(path: PathLike) -> Any:
    yaml_path = Path(path)
    try:
        with yaml_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise YamlError(f"YAML file not found: {yaml_path}") from exc
    except yaml.YAMLError as exc:
        raise YamlError(f"Invalid YAML in {yaml_path}: {exc}") from exc


def load_yaml_mapping(path: PathLike) -> Mapping[str, Any]:
    data = load_yaml(path)
    if data is None:
        raise YamlError(f"YAML file is empty: {path}")
    if not isinstance(data, Mapping):
        raise YamlError(f"Expected a YAML mapping at top level: {path}")
    return cast(Mapping[str, Any], data)


def dump_yaml(data: Any, path: PathLike) -> None:
    yaml_path = Path(path)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with yaml_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)
