"""Tests for the deterministic grader."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from agent_runner_harness.grading.deterministic import grade_deterministic
from agent_runner_shared.models import AcceptanceCriterion


def _make_criterion(**kwargs) -> AcceptanceCriterion:
    """Build an AcceptanceCriterion with defaults."""
    defaults = {
        "id": "test-crit",
        "description": "Test criterion",
        "kind": "file_exists",
    }
    defaults.update(kwargs)
    return AcceptanceCriterion.model_validate(defaults)


class TestFileExistsCriterion:
    def test_passes_when_file_exists(self, tmp_path: Path) -> None:
        """file_exists criterion passes when file is present."""
        (tmp_path / "story.yaml").write_text("x: 1", encoding="utf-8")
        criterion = _make_criterion(kind="file_exists", path="story.yaml")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "events.jsonl")
        assert results[0]["passed"] is True

    def test_fails_when_file_missing(self, tmp_path: Path) -> None:
        """file_exists criterion fails when file is absent."""
        criterion = _make_criterion(kind="file_exists", path="missing.yaml")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "events.jsonl")
        assert results[0]["passed"] is False

    def test_result_has_required_keys(self, tmp_path: Path) -> None:
        """Result dict has id, kind, passed, detail keys."""
        criterion = _make_criterion(kind="file_exists", path="x.yaml")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "events.jsonl")
        r = results[0]
        assert "id" in r
        assert "kind" in r
        assert "passed" in r
        assert "detail" in r


class TestEventAssertionCriterion:
    def _write_event_log(self, path: Path, events: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

    def test_passes_when_event_matches(self, tmp_path: Path) -> None:
        """event_assertion passes when matching event is in log."""
        log = tmp_path / "events.jsonl"
        self._write_event_log(log, [
            {"kind": "eval.pass", "stage": "qa", "run_id": "r1"},
        ])
        criterion = _make_criterion(
            kind="event_assertion",
            event={"kind": "eval.pass", "stage": "qa"},
        )
        results = grade_deterministic([criterion], tmp_path, log)
        assert results[0]["passed"] is True

    def test_fails_when_no_match(self, tmp_path: Path) -> None:
        """event_assertion fails when no matching event is found."""
        log = tmp_path / "events.jsonl"
        self._write_event_log(log, [
            {"kind": "run.start", "stage": None},
        ])
        criterion = _make_criterion(
            kind="event_assertion",
            event={"kind": "eval.pass", "stage": "qa"},
        )
        results = grade_deterministic([criterion], tmp_path, log)
        assert results[0]["passed"] is False

    def test_fails_when_log_missing(self, tmp_path: Path) -> None:
        """event_assertion fails gracefully when log file doesn't exist."""
        criterion = _make_criterion(
            kind="event_assertion",
            event={"kind": "eval.pass"},
        )
        results = grade_deterministic([criterion], tmp_path, tmp_path / "no.jsonl")
        assert results[0]["passed"] is False

    def test_partial_match(self, tmp_path: Path) -> None:
        """event_assertion does subset matching — extra fields in log are ok."""
        log = tmp_path / "events.jsonl"
        self._write_event_log(log, [
            {"kind": "eval.pass", "stage": "qa", "extra_field": "ignored"},
        ])
        criterion = _make_criterion(
            kind="event_assertion",
            event={"kind": "eval.pass"},
        )
        results = grade_deterministic([criterion], tmp_path, log)
        assert results[0]["passed"] is True

    def test_multiple_criteria(self, tmp_path: Path) -> None:
        """Multiple criteria are each evaluated independently."""
        log = tmp_path / "events.jsonl"
        self._write_event_log(log, [
            {"kind": "eval.pass", "stage": "qa"},
        ])
        (tmp_path / "story.yaml").write_text("acs: []", encoding="utf-8")
        criteria = [
            _make_criterion(id="c1", kind="file_exists", path="story.yaml"),
            _make_criterion(id="c2", kind="event_assertion", event={"kind": "eval.pass"}),
            _make_criterion(id="c3", kind="file_exists", path="missing.yaml"),
        ]
        results = grade_deterministic(criteria, tmp_path, log)
        assert len(results) == 3
        assert results[0]["passed"] is True
        assert results[1]["passed"] is True
        assert results[2]["passed"] is False


class TestScriptCriterion:
    def test_passes_with_exit_zero_script(self, tmp_path: Path) -> None:
        """script criterion passes when script exits 0."""
        script = tmp_path / "check.py"
        script.write_text("import sys; sys.exit(0)", encoding="utf-8")
        criterion = _make_criterion(kind="script", script="check.py")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "e.jsonl",
                                      task_dir=tmp_path)
        assert results[0]["passed"] is True

    def test_fails_with_nonzero_exit(self, tmp_path: Path) -> None:
        """script criterion fails when script exits non-zero."""
        script = tmp_path / "check.py"
        script.write_text("import sys; sys.exit(1)", encoding="utf-8")
        criterion = _make_criterion(kind="script", script="check.py")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "e.jsonl",
                                      task_dir=tmp_path)
        assert results[0]["passed"] is False

    def test_fails_when_script_missing(self, tmp_path: Path) -> None:
        """script criterion fails when script file doesn't exist."""
        criterion = _make_criterion(kind="script", script="nonexistent.py")
        results = grade_deterministic([criterion], tmp_path, tmp_path / "e.jsonl",
                                      task_dir=tmp_path)
        assert results[0]["passed"] is False
