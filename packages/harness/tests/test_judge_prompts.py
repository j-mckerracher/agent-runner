"""Tests for judge prompt loading and rendering (pG-judge-prompts)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_runner_harness.grading.prompts import (
    PROMPT_VERSION,
    load_prompt,
    render_rubric_prompt,
)


class TestPromptVersion:
    def test_version_is_string(self) -> None:
        assert isinstance(PROMPT_VERSION, str)
        assert PROMPT_VERSION == "1"


class TestLoadPrompt:
    def test_load_rubric_template(self) -> None:
        text = load_prompt("rubric_template")
        assert len(text) > 100

    def test_load_system(self) -> None:
        text = load_prompt("system")
        assert len(text) > 50

    def test_missing_prompt_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_prompt("does_not_exist")


def _make_criterion(
    description: str = "The report must include a cost breakdown.",
    scale: str = "0-3",
    threshold: float = 2.0,
) -> MagicMock:
    c = MagicMock()
    c.description = description
    c.scale = scale
    c.threshold = threshold
    return c


class TestRenderRubricPrompt:
    def test_all_placeholders_substituted(self) -> None:
        criterion = _make_criterion()
        rendered = render_rubric_prompt(
            criterion=criterion,
            task="Write a cost analysis report",
            artifact_summary="output/report.md",
            event_log_excerpt="[event#1] file written: output/report.md",
        )
        # No placeholders should remain
        assert "{{task_description}}" not in rendered
        assert "{{criterion_description}}" not in rendered
        assert "{{scale}}" not in rendered
        assert "{{threshold}}" not in rendered
        assert "{{artifact_summary}}" not in rendered
        assert "{{event_log_excerpt}}" not in rendered

    def test_values_appear_in_output(self) -> None:
        criterion = _make_criterion(
            description="Check for cost breakdown",
            scale="0-5",
            threshold=3.0,
        )
        rendered = render_rubric_prompt(
            criterion=criterion,
            task="My special task",
            artifact_summary="report.pdf, data.csv",
            event_log_excerpt="stage complete",
        )
        assert "My special task" in rendered
        assert "Check for cost breakdown" in rendered
        assert "0-5" in rendered
        assert "3.0" in rendered
        assert "report.pdf, data.csv" in rendered
        assert "stage complete" in rendered

    def test_json_instruction_present(self) -> None:
        criterion = _make_criterion()
        rendered = render_rubric_prompt(
            criterion=criterion,
            task="task",
            artifact_summary="none",
            event_log_excerpt="",
        )
        # Must instruct judge to return JSON with required fields
        assert "score" in rendered
        assert "rationale" in rendered
        assert "evidence_refs" in rendered
        assert "JSON" in rendered or "json" in rendered.lower()

    def test_forbids_questions_instruction_present(self) -> None:
        criterion = _make_criterion()
        rendered = render_rubric_prompt(
            criterion=criterion,
            task="task",
            artifact_summary="none",
            event_log_excerpt="",
        )
        # Must contain prohibition on asking questions
        assert "question" in rendered.lower() or "clarif" in rendered.lower()

    def test_task_object_with_title_attr(self) -> None:
        criterion = _make_criterion()
        task_obj = MagicMock()
        task_obj.title = "Report Generation Task"
        rendered = render_rubric_prompt(
            criterion=criterion,
            task=task_obj,
            artifact_summary="x",
            event_log_excerpt="",
        )
        assert "Report Generation Task" in rendered

    def test_calibration_anchors_present(self) -> None:
        criterion = _make_criterion()
        rendered = render_rubric_prompt(
            criterion=criterion,
            task="task",
            artifact_summary="none",
            event_log_excerpt="",
        )
        assert "anchor" in rendered.lower() or "score 0" in rendered.lower() or "lowest" in rendered.lower()

    def test_system_prompt_is_json_only(self) -> None:
        system = load_prompt("system")
        assert "JSON" in system or "json" in system.lower()
        assert "question" in system.lower() or "ask" in system.lower()
