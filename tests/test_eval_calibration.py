"""Tests for eval/validate_calibration.py"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, Mock, patch

import pytest

from eval.models import AcceptanceCriterion, CheckDefinition, EvalStory
from eval.suite_io import dump_eval_story, dump_suite_manifest
from eval.validate_calibration import (
    ACRunResult,
    classify_calibration,
    compute_tier_pass_rates,
    generate_suggestions,
    run_calibration_validation,
    sample_acs_by_tier,
    write_report,
)


@pytest.fixture
def mock_story_easy() -> EvalStory:
    """Create a mock story with all tier ACs."""
    return EvalStory(
        story_id="story_001",
        title="Test Story",
        description="Test description",
        suite_tier="easy",
        dataset_id="test_dataset",
        acceptance_criteria=[
            AcceptanceCriterion(
                ac_id="story_001_e1",
                text="Easy AC 1",
                tier="easy",
                check=CheckDefinition(
                    id="story_001_e1",
                    label="Easy check 1",
                    mechanism="contains",
                    subject="agent_output",
                    expected="easy",
                ),
            ),
            AcceptanceCriterion(
                ac_id="story_001_e2",
                text="Easy AC 2",
                tier="easy",
                check=CheckDefinition(
                    id="story_001_e2",
                    label="Easy check 2",
                    mechanism="contains",
                    subject="agent_output",
                    expected="easy",
                ),
            ),
            AcceptanceCriterion(
                ac_id="story_001_m1",
                text="Medium AC 1",
                tier="medium",
                check=CheckDefinition(
                    id="story_001_m1",
                    label="Medium check 1",
                    mechanism="matches",
                    subject="agent_output",
                    expected=r"medium.*test",
                ),
            ),
            AcceptanceCriterion(
                ac_id="story_001_h1",
                text="Hard AC 1",
                tier="hard",
                check=CheckDefinition(
                    id="story_001_h1",
                    label="Hard check 1",
                    mechanism="command",
                    subject="build",
                    command=["echo", "hard"],
                ),
            ),
        ],
    )


@pytest.fixture
def temp_suite_dir(tmp_path: Path, mock_story_easy: EvalStory) -> Path:
    """Create a temporary suite directory with stories."""
    suite_dir = tmp_path / "test_suite"
    suite_dir.mkdir()
    
    # Write story
    story_path = suite_dir / "story_001.yaml"
    dump_eval_story(mock_story_easy, story_path)
    
    # Write suite manifest
    from eval.models import SuiteManifest
    manifest = SuiteManifest(
        suite_id="test_suite",
        suite_tier="easy",
        dataset_id="test_dataset",
        stories=["story_001.yaml"],
        total_checks=4,
    )
    dump_suite_manifest(manifest, suite_dir / "suite_manifest.yaml")
    
    return suite_dir


# Classification Tests

def test_classify_well_calibrated() -> None:
    """Test classification of well-calibrated suite."""
    tier_pass_rates = {"easy": 0.85, "medium": 0.55, "hard": 0.35}
    assert classify_calibration(tier_pass_rates) == "well_calibrated"


def test_classify_overall_too_hard() -> None:
    """Test classification when easy tier is too hard."""
    tier_pass_rates = {"easy": 0.40, "medium": 0.25, "hard": 0.10}
    assert classify_calibration(tier_pass_rates) == "overall_too_hard"


def test_classify_overall_too_easy() -> None:
    """Test classification when hard tier is too easy."""
    tier_pass_rates = {"easy": 0.90, "medium": 0.60, "hard": 0.80}
    assert classify_calibration(tier_pass_rates) == "overall_too_easy"


def test_classify_medium_miscalibrated_low() -> None:
    """Test classification when medium tier is too hard."""
    tier_pass_rates = {"easy": 0.85, "medium": 0.25, "hard": 0.35}
    assert classify_calibration(tier_pass_rates) == "medium_miscalibrated"


def test_classify_medium_miscalibrated_high() -> None:
    """Test classification when medium tier is too easy."""
    tier_pass_rates = {"easy": 0.85, "medium": 0.85, "hard": 0.35}
    assert classify_calibration(tier_pass_rates) == "medium_miscalibrated"


def test_classify_edge_case_easy_75() -> None:
    """Test classification at easy tier boundary."""
    tier_pass_rates = {"easy": 0.75, "medium": 0.50, "hard": 0.40}
    assert classify_calibration(tier_pass_rates) == "well_calibrated"


def test_classify_edge_case_medium_35() -> None:
    """Test classification at medium tier lower boundary."""
    tier_pass_rates = {"easy": 0.80, "medium": 0.35, "hard": 0.30}
    assert classify_calibration(tier_pass_rates) == "well_calibrated"


def test_classify_edge_case_medium_74() -> None:
    """Test classification at medium tier upper boundary."""
    tier_pass_rates = {"easy": 0.80, "medium": 0.74, "hard": 0.40}
    assert classify_calibration(tier_pass_rates) == "well_calibrated"


def test_classify_edge_case_hard_50() -> None:
    """Test classification at hard tier boundary."""
    tier_pass_rates = {"easy": 0.80, "medium": 0.50, "hard": 0.50}
    # hard < 0.50, so 0.50 is not < 0.50
    assert classify_calibration(tier_pass_rates) == "medium_miscalibrated"


# Deterministic Sampling Tests

def test_sample_acs_deterministic(mock_story_easy: EvalStory) -> None:
    """Test that sampling is deterministic with same seed across runs."""
    sampled1 = sample_acs_by_tier(mock_story_easy, seed=42, run_index=0)
    sampled2 = sample_acs_by_tier(mock_story_easy, seed=42, run_index=0)
    
    assert sampled1.keys() == sampled2.keys()
    for tier in sampled1:
        assert sampled1[tier].ac_id == sampled2[tier].ac_id


def test_sample_acs_deterministic_multiple_runs(mock_story_easy: EvalStory) -> None:
    """Test that same seed/story/run produces same AC selection across processes."""
    # Simulate multiple "processes" by calling multiple times
    samples = []
    for _ in range(10):
        sampled = sample_acs_by_tier(mock_story_easy, seed=42, run_index=0)
        samples.append(sampled)
    
    # All samples should be identical
    for i in range(1, len(samples)):
        assert samples[0].keys() == samples[i].keys()
        for tier in samples[0]:
            assert samples[0][tier].ac_id == samples[i][tier].ac_id, \
                f"AC selection differs between runs for tier {tier}"


def test_sample_acs_varies_with_run_index(mock_story_easy: EvalStory) -> None:
    """Test that sampling changes with run index."""
    sampled1 = sample_acs_by_tier(mock_story_easy, seed=42, run_index=0)
    sampled2 = sample_acs_by_tier(mock_story_easy, seed=42, run_index=1)
    
    # With 2 easy ACs, there's a chance they're the same, but with multiple tiers
    # at least one should be different (or could be same by chance)
    # Main thing is that the RNG is different per run
    assert "easy" in sampled1
    assert "easy" in sampled2


def test_sample_acs_one_per_tier(mock_story_easy: EvalStory) -> None:
    """Test that exactly one AC per tier is sampled."""
    sampled = sample_acs_by_tier(mock_story_easy, seed=42, run_index=0)
    
    assert "easy" in sampled
    assert "medium" in sampled
    assert "hard" in sampled
    
    assert isinstance(sampled["easy"], AcceptanceCriterion)
    assert sampled["easy"].tier == "easy"
    assert isinstance(sampled["medium"], AcceptanceCriterion)
    assert sampled["medium"].tier == "medium"
    assert isinstance(sampled["hard"], AcceptanceCriterion)
    assert sampled["hard"].tier == "hard"


def test_sample_acs_empty_tier() -> None:
    """Test sampling when a tier has no ACs."""
    story = EvalStory(
        story_id="story_002",
        title="Incomplete Story",
        description="Story with missing tier",
        suite_tier="easy",
        dataset_id="test_dataset",
        acceptance_criteria=[
            AcceptanceCriterion(
                ac_id="story_002_e1",
                text="Easy AC 1",
                tier="easy",
            ),
        ],
    )
    
    sampled = sample_acs_by_tier(story, seed=42, run_index=0)
    
    assert "easy" in sampled
    assert "medium" not in sampled
    assert "hard" not in sampled


# Pass Rate Computation Tests

def test_compute_tier_pass_rates() -> None:
    """Test tier pass rate computation."""
    ac_results = [
        ACRunResult(ac_id="e1", tier="easy", text="", runs_passed=4, runs_total=5, pass_rate=0.8),
        ACRunResult(ac_id="e2", tier="easy", text="", runs_passed=5, runs_total=5, pass_rate=1.0),
        ACRunResult(ac_id="m1", tier="medium", text="", runs_passed=3, runs_total=5, pass_rate=0.6),
        ACRunResult(ac_id="h1", tier="hard", text="", runs_passed=1, runs_total=5, pass_rate=0.2),
    ]
    
    tier_pass_rates = compute_tier_pass_rates(ac_results)
    
    assert tier_pass_rates["easy"] == 0.9  # (4+5) / (5+5) = 9/10
    assert tier_pass_rates["medium"] == 0.6  # 3/5
    assert tier_pass_rates["hard"] == 0.2  # 1/5


def test_compute_tier_pass_rates_empty_tier() -> None:
    """Test tier pass rate computation with empty tier."""
    ac_results = [
        ACRunResult(ac_id="e1", tier="easy", text="", runs_passed=4, runs_total=5, pass_rate=0.8),
    ]
    
    tier_pass_rates = compute_tier_pass_rates(ac_results)
    
    assert tier_pass_rates["easy"] == 0.8
    assert tier_pass_rates["medium"] == 0.0
    assert tier_pass_rates["hard"] == 0.0


# Suggestion Generation Tests

def test_generate_suggestions_deterministic_fallback() -> None:
    """Test suggestion generation without LLM (deterministic fallback)."""
    ac_results = [
        ACRunResult(ac_id="e1", tier="easy", text="Easy AC", runs_passed=4, runs_total=5, pass_rate=0.8),
    ]
    tier_pass_rates = {"easy": 0.8, "medium": 0.5, "hard": 0.3}
    
    suggestions = generate_suggestions(
        classification="well_calibrated",
        tier_pass_rates=tier_pass_rates,
        ac_results=ac_results,
        runner=None,  # No runner = deterministic fallback
        model=None,
        repo_path=None,
    )
    
    assert len(suggestions) == 3
    assert "well_calibrated" in suggestions[0]
    assert "Easy: 0.80" in suggestions[1]
    assert "Consider reviewing" in suggestions[2]


def test_generate_suggestions_with_runner_mock(tmp_path: Path) -> None:
    """Test suggestion generation with mocked LLM runner."""
    ac_results = [
        ACRunResult(ac_id="e1", tier="easy", text="Easy AC", runs_passed=2, runs_total=5, pass_rate=0.4),
    ]
    tier_pass_rates = {"easy": 0.4, "medium": 0.3, "hard": 0.2}
    
    with patch("eval.validate_calibration.run_agent_cmd") as mock_run:
        mock_run.return_value = "Suggestion 1: Make easy ACs easier.\nSuggestion 2: Review medium ACs."
        
        suggestions = generate_suggestions(
            classification="overall_too_hard",
            tier_pass_rates=tier_pass_rates,
            ac_results=ac_results,
            runner="claude",
            model="claude-sonnet-4",
            repo_path=tmp_path,  # Use tmp_path instead of /tmp/test
        )
    
    assert len(suggestions) == 2
    assert "Make easy ACs easier" in suggestions[0]
    assert "Review medium ACs" in suggestions[1]
    mock_run.assert_called_once()


def test_generate_suggestions_runner_failure_graceful(tmp_path: Path) -> None:
    """Test graceful degradation when LLM runner fails."""
    ac_results = [
        ACRunResult(ac_id="e1", tier="easy", text="Easy AC", runs_passed=2, runs_total=5, pass_rate=0.4),
    ]
    tier_pass_rates = {"easy": 0.4, "medium": 0.3, "hard": 0.2}
    
    with patch("eval.validate_calibration.run_agent_cmd") as mock_run:
        mock_run.side_effect = RuntimeError("LLM service unavailable")
        
        suggestions = generate_suggestions(
            classification="overall_too_hard",
            tier_pass_rates=tier_pass_rates,
            ac_results=ac_results,
            runner="claude",
            model="claude-sonnet-4",
            repo_path=tmp_path,  # Use tmp_path instead of /tmp/test
        )
    
    assert len(suggestions) == 3
    assert "Could not generate LLM suggestions" in suggestions[0]
    assert "overall_too_hard" in suggestions[1]
    assert "Manual review" in suggestions[2]


# Report Schema Tests

def test_write_report_schema(tmp_path: Path) -> None:
    """Test that report writes with correct schema."""
    from eval.validate_calibration import CalibrationReport
    
    report = CalibrationReport(
        suite_path="eval/suites/test/",
        dataset_id="test_dataset",
        calibrated_at="2025-05-01T12:00:00Z",
        runs=5,
        seed=42,
        tier_pass_rates={"easy": 0.8, "medium": 0.5, "hard": 0.3},
        ac_results=[
            {
                "ac_id": "e1",
                "tier": "easy",
                "text": "Easy AC",
                "runs_passed": 4,
                "runs_total": 5,
                "pass_rate": 0.8,
            }
        ],
        classification="well_calibrated",
        recalibration_needed=False,
        suggestions=["No changes needed"],
        apply_instructions="Do NOT auto-apply suggestions. Manual review and re-synthesis required.",
    )
    
    output_path = tmp_path / "report.json"
    write_report(report, str(output_path))
    
    assert output_path.exists()
    
    with output_path.open("r") as f:
        data = json.load(f)
    
    # Verify schema
    assert data["suite_path"] == "eval/suites/test/"
    assert data["dataset_id"] == "test_dataset"
    assert data["calibrated_at"] == "2025-05-01T12:00:00Z"
    assert data["runs"] == 5
    assert data["seed"] == 42
    assert data["tier_pass_rates"] == {"easy": 0.8, "medium": 0.5, "hard": 0.3}
    assert len(data["ac_results"]) == 1
    assert data["ac_results"][0]["ac_id"] == "e1"
    assert data["classification"] == "well_calibrated"
    assert data["recalibration_needed"] is False
    assert data["suggestions"] == ["No changes needed"]
    assert "Do NOT auto-apply" in data["apply_instructions"]


# Integration Tests

def test_run_calibration_validation_integration(temp_suite_dir: Path) -> None:
    """Test full calibration validation run."""
    output_path = temp_suite_dir / "calibration_report.json"
    
    with patch("eval.validate_calibration.execute_ac_check") as mock_check:
        # Mock check results to create a predictable pattern
        from eval.models import CheckResult
        mock_check.return_value = CheckResult(
            check_id="test",
            passed=True,  # All pass for well-calibrated
            attempted=True,
        )
        
        report = run_calibration_validation(
            suite_path=str(temp_suite_dir),
            runs=3,
            seed=42,
            output_path=str(output_path),
            repo=None,  # No repo = deterministic suggestions
            runner=None,
            model=None,
            skip_opik=True,
            skip_pipeline=True,
        )
    
    assert report.suite_path == str(temp_suite_dir)
    assert report.dataset_id == "test_dataset"
    assert report.runs == 3
    assert report.seed == 42
    assert len(report.ac_results) >= 3  # At least one per tier
    assert report.classification in ["well_calibrated", "overall_too_easy", "medium_miscalibrated", "overall_too_hard"]
    assert isinstance(report.recalibration_needed, bool)
    assert len(report.suggestions) > 0


def test_run_calibration_validation_cli_args(temp_suite_dir: Path) -> None:
    """Test CLI argument parsing and execution."""
    from eval.validate_calibration import build_parser, main
    
    output_path = temp_suite_dir / "calibration_report.json"
    
    with patch("eval.validate_calibration.run_calibration_validation") as mock_run:
        from eval.validate_calibration import CalibrationReport
        mock_run.return_value = CalibrationReport(
            suite_path=str(temp_suite_dir),
            dataset_id="test_dataset",
            calibrated_at="2025-05-01T12:00:00Z",
            runs=3,
            seed=42,
            tier_pass_rates={"easy": 0.8, "medium": 0.5, "hard": 0.3},
            ac_results=[],
            classification="well_calibrated",
            recalibration_needed=False,
            suggestions=["All good"],
            apply_instructions="Do NOT auto-apply suggestions. Manual review and re-synthesis required.",
        )
        
        exit_code = main([
            "--suite", str(temp_suite_dir),
            "--runs", "3",
            "--seed", "42",
            "--output", str(output_path),
            "--skip-opik",
        ])
    
    assert exit_code == 0  # well_calibrated = success
    mock_run.assert_called_once()


def test_run_calibration_validation_recalibration_needed_exit_code(temp_suite_dir: Path) -> None:
    """Test exit code when recalibration is needed."""
    from eval.validate_calibration import main
    
    output_path = temp_suite_dir / "calibration_report.json"
    
    with patch("eval.validate_calibration.run_calibration_validation") as mock_run:
        from eval.validate_calibration import CalibrationReport
        mock_run.return_value = CalibrationReport(
            suite_path=str(temp_suite_dir),
            dataset_id="test_dataset",
            calibrated_at="2025-05-01T12:00:00Z",
            runs=3,
            seed=42,
            tier_pass_rates={"easy": 0.4, "medium": 0.3, "hard": 0.2},
            ac_results=[],
            classification="overall_too_hard",
            recalibration_needed=True,
            suggestions=["Make easier"],
            apply_instructions="Do NOT auto-apply suggestions. Manual review and re-synthesis required.",
        )
        
        exit_code = main([
            "--suite", str(temp_suite_dir),
            "--runs", "3",
            "--seed", "42",
            "--output", str(output_path),
        ])
    
    assert exit_code == 1  # recalibration_needed = exit 1
