"""Tests for the AC migrator's HTML parser."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add tools dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tools.ac_migrator.cli import extract_ac_candidates, _slugify, build_task_dict


SAMPLE_HTML_WITH_HEADING = """
<div>
  <p>As a developer, I need automated testing so that bugs are caught early.</p>
  <h2>Acceptance Criteria</h2>
  <ul>
    <li>Given a valid input, when the function is called, then it returns the correct result.</li>
    <li>Given an invalid input, when the function is called, then it raises a ValueError.</li>
    <li>Unit test coverage must be at least 80%.</li>
  </ul>
</div>
"""

SAMPLE_HTML_NO_HEADING = """
<ul>
  <li>The API response time must be under 200ms.</li>
  <li>Error messages must be human-readable.</li>
</ul>
"""

SAMPLE_HTML_AC_FIELD = """
<ul>
  <li>story.yaml is present in the artifact directory</li>
  <li>At least 2 ACs are extracted and structured correctly</li>
  <li>eval.pass event is emitted at qa stage</li>
</ul>
"""


class TestExtractAcCandidates:
    def test_extracts_from_heading_section(self) -> None:
        """Extracts list items that appear after the Acceptance Criteria heading."""
        candidates = extract_ac_candidates(SAMPLE_HTML_WITH_HEADING)
        assert len(candidates) == 3
        assert any("returns the correct result" in c for c in candidates)
        assert any("raises a ValueError" in c for c in candidates)
        assert any("80%" in c for c in candidates)

    def test_extracts_without_heading(self) -> None:
        """Falls back to all list items when no heading is found."""
        candidates = extract_ac_candidates(SAMPLE_HTML_NO_HEADING)
        assert len(candidates) == 2
        assert any("200ms" in c for c in candidates)
        assert any("human-readable" in c for c in candidates)

    def test_extracts_from_ac_field(self) -> None:
        """Extracts from the dedicated AcceptanceCriteria HTML field."""
        candidates = extract_ac_candidates(SAMPLE_HTML_AC_FIELD)
        assert len(candidates) == 3
        assert any("story.yaml" in c for c in candidates)
        assert any("eval.pass" in c for c in candidates)

    def test_strips_html_tags(self) -> None:
        """Extracted text has no HTML tags."""
        candidates = extract_ac_candidates(SAMPLE_HTML_WITH_HEADING)
        for c in candidates:
            assert "<" not in c
            assert ">" not in c

    def test_empty_html_returns_empty_list(self) -> None:
        """Empty HTML returns an empty list."""
        candidates = extract_ac_candidates("")
        assert candidates == []

    def test_html_without_list_items_returns_empty(self) -> None:
        """HTML with only a paragraph returns empty when no list items."""
        candidates = extract_ac_candidates("<p>No acceptance criteria here.</p>")
        assert candidates == []

    def test_combined_description_and_ac_field(self) -> None:
        """Combining description + AC field yields all criteria."""
        combined = SAMPLE_HTML_WITH_HEADING + SAMPLE_HTML_AC_FIELD
        candidates = extract_ac_candidates(combined)
        # Heading found, only items after the heading are extracted
        assert len(candidates) >= 3


class TestSlugify:
    def test_basic_slug(self) -> None:
        assert _slugify("Hello World") == "hello-world"

    def test_strips_special_chars(self) -> None:
        slug = _slugify("AC #1: must return <200ms")
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)

    def test_max_length(self) -> None:
        long_text = "a" * 100
        assert len(_slugify(long_text)) <= 50


class TestBuildTaskDict:
    def test_deterministic_only(self) -> None:
        task = build_task_dict("my-task", "My Task", ["AC 1", "AC 2"], [])
        assert task["id"] == "my-task"
        assert "deterministic" in task["acceptance_criteria"]
        assert "rubric" not in task["acceptance_criteria"]
        assert len(task["acceptance_criteria"]["deterministic"]) == 2

    def test_rubric_only(self) -> None:
        task = build_task_dict("my-task", "My Task", [], ["AC 1"])
        assert "rubric" in task["acceptance_criteria"]
        assert "deterministic" not in task["acceptance_criteria"]
        rub = task["acceptance_criteria"]["rubric"][0]
        assert rub["scale"] == "0-3"
        assert rub["threshold"] == 2

    def test_mixed(self) -> None:
        task = build_task_dict("my-task", "My Task", ["Det AC"], ["Rub AC"])
        acs = task["acceptance_criteria"]
        assert "deterministic" in acs
        assert "rubric" in acs

    def test_task_structure(self) -> None:
        task = build_task_dict("test-task", "Test", [], ["AC"])
        assert task["version"] == 1
        assert task["substrate"]["ref"] == "baseline-2026-04-16"
        assert task["workflow"]["id"] == "standard"
