"""Task-corpus loader and validator.

Reads task.yaml files from a corpus directory and validates them against
the shared task.schema.json JSON Schema.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
import jsonschema

from agent_runner_shared.models import Task

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "packages/shared/agent_runner_shared/schemas/task.schema.json"
)


def _load_schema() -> dict[str, Any]:
    """Load the task JSON Schema from the shared package."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_task_file(path: Path) -> None:
    """Validate a task.yaml file against the task JSON Schema.

    Args:
        path: Path to the task YAML file.

    Raises:
        jsonschema.ValidationError: If the task file is invalid.
        ValueError: If the file cannot be parsed.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = _load_schema()
    jsonschema.validate(instance=raw, schema=schema)


def load_task(task_dir: Path) -> Task:
    """Load and validate a Task from a task directory.

    Args:
        task_dir: Directory containing task.yaml.

    Returns:
        Validated Task model.

    Raises:
        FileNotFoundError: If task.yaml is missing.
        jsonschema.ValidationError: If validation fails.
    """
    task_dir = Path(task_dir)
    task_file = task_dir / "task.yaml"
    if not task_file.exists():
        raise FileNotFoundError(f"task.yaml not found in {task_dir}")
    validate_task_file(task_file)
    raw = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    return Task.model_validate(raw)


def load_all(corpus_dir: Path) -> list[Task]:
    """Load all tasks from a corpus directory.

    Scans for subdirectories containing task.yaml files.

    Args:
        corpus_dir: Root of the task corpus.

    Returns:
        List of validated Task models.
    """
    corpus_dir = Path(corpus_dir)
    tasks: list[Task] = []
    for entry in sorted(corpus_dir.iterdir()):
        if entry.is_dir() and (entry / "task.yaml").exists():
            tasks.append(load_task(entry))
    return tasks
