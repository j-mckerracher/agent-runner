"""Tests for the task corpus loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_runner_harness.corpus import load_task, load_all, validate_task_file
from agent_runner_shared.models import Task

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CORPUS_DIR = REPO_ROOT / "task-corpus"


class TestLoadTask:
    def test_load_ado_task(self) -> None:
        """Load the ado-normalize-embedded-ac seed task."""
        task = load_task(CORPUS_DIR / "ado-normalize-embedded-ac")
        assert isinstance(task, Task)
        assert task.id == "ado-normalize-embedded-ac"
        assert task.version == 1
        assert task.difficulty == "medium"

    def test_load_simple_readme_task(self) -> None:
        """Load the simple-readme-update seed task."""
        task = load_task(CORPUS_DIR / "simple-readme-update")
        assert isinstance(task, Task)
        assert task.id == "simple-readme-update"
        assert task.difficulty == "easy"

    def test_load_refactor_task(self) -> None:
        """Load the refactor-util-module seed task."""
        task = load_task(CORPUS_DIR / "refactor-util-module")
        assert isinstance(task, Task)
        assert task.id == "refactor-util-module"
        assert task.difficulty == "hard"

    def test_missing_task_raises(self, tmp_path: Path) -> None:
        """load_task raises FileNotFoundError for missing task.yaml."""
        with pytest.raises(FileNotFoundError):
            load_task(tmp_path / "nonexistent-task")

    def test_invalid_task_raises(self, tmp_path: Path) -> None:
        """load_task raises ValidationError for malformed task.yaml."""
        task_dir = tmp_path / "bad-task"
        task_dir.mkdir()
        (task_dir / "task.yaml").write_text(
            yaml.dump({"id": "bad", "title": "Bad", "version": 1}),
            encoding="utf-8",
        )
        with pytest.raises(Exception):  # jsonschema.ValidationError
            load_task(task_dir)


class TestLoadAll:
    def test_loads_all_seed_tasks(self) -> None:
        """load_all returns at least the 3 seed tasks."""
        tasks = load_all(CORPUS_DIR)
        task_ids = {t.id for t in tasks}
        assert "ado-normalize-embedded-ac" in task_ids
        assert "simple-readme-update" in task_ids
        assert "refactor-util-module" in task_ids

    def test_all_tasks_are_task_instances(self) -> None:
        """All loaded tasks are Task model instances."""
        tasks = load_all(CORPUS_DIR)
        for task in tasks:
            assert isinstance(task, Task)


class TestValidateTaskFile:
    def test_validates_seed_tasks(self) -> None:
        """validate_task_file passes for all seed tasks."""
        for task_dir in CORPUS_DIR.iterdir():
            if task_dir.is_dir() and (task_dir / "task.yaml").exists():
                validate_task_file(task_dir / "task.yaml")

    def test_rejects_missing_required_fields(self, tmp_path: Path) -> None:
        """validate_task_file raises on missing required fields."""
        bad_file = tmp_path / "task.yaml"
        bad_file.write_text(yaml.dump({"title": "No ID"}), encoding="utf-8")
        with pytest.raises(Exception):
            validate_task_file(bad_file)

    def test_rejects_invalid_difficulty(self, tmp_path: Path) -> None:
        """validate_task_file raises on invalid difficulty value."""
        bad_file = tmp_path / "task.yaml"
        bad_data = {
            "id": "test-task",
            "version": 1,
            "title": "Test",
            "difficulty": "impossible",
            "substrate": {"ref": "baseline-2026-04-16"},
            "workflow": {"id": "standard", "version": 1},
            "acceptance_criteria": {"deterministic": [
                {"id": "ac1", "description": "test", "kind": "file_exists", "path": "out.yaml"}
            ]},
        }
        bad_file.write_text(yaml.dump(bad_data), encoding="utf-8")
        with pytest.raises(Exception):
            validate_task_file(bad_file)
