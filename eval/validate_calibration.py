"""Calibration validation CLI for difficulty-tier evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

if __package__ in {None, ""}:  # pragma: no cover - exercised by direct CLI use.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval.check_helpers import run_check
    from eval.models import AcceptanceCriterion, CheckResult, EvalStory, SuiteTier
    from eval.scoring import score_for_difficulty
    from eval.suite_io import load_eval_story, load_suite_manifest
    from core.run_cmds import run_agent_cmd
else:
    from .check_helpers import run_check
    from .models import AcceptanceCriterion, CheckResult, EvalStory, SuiteTier
    from .scoring import score_for_difficulty
    from .suite_io import load_eval_story, load_suite_manifest
    from core.run_cmds import run_agent_cmd


CalibrationClassification = Literal[
    "well_calibrated",
    "overall_too_hard",
    "overall_too_easy",
    "medium_miscalibrated",
]


@dataclass(frozen=True)
class ACRunResult:
    """Single AC execution result across multiple runs."""

    ac_id: str
    tier: SuiteTier
    text: str
    runs_passed: int
    runs_total: int
    pass_rate: float


@dataclass(frozen=True)
class CalibrationReport:
    """Complete calibration validation report."""

    suite_path: str
    dataset_id: str
    calibrated_at: str
    runs: int
    seed: int
    tier_pass_rates: Dict[str, float]
    ac_results: List[Dict[str, Any]]
    classification: CalibrationClassification
    recalibration_needed: bool
    suggestions: List[str]
    apply_instructions: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate eval suite calibration via deterministic AC sampling."
    )
    parser.add_argument("--suite", required=True, help="Path to eval/suites/ or suite manifest")
    parser.add_argument("--runs", type=int, default=5, help="Number of sampling runs")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic sampling")
    parser.add_argument(
        "--output",
        default="eval/suites/calibration_report.json",
        help="Output path for calibration report JSON",
    )
    parser.add_argument("--repo", help="Target repository path (optional, for suggestion generation)")
    parser.add_argument("--runner", metavar="RUNNER",
                        help="Runner for suggestions: 'claude', 'copilot', 'gemini', or a copilot alias like 'copilot-gemma4'")
    parser.add_argument("--model", help="Model for suggestion generation")
    parser.add_argument("--skip-opik", action="store_true", help="Skip Opik reporting")
    parser.add_argument("--skip-pipeline", action="store_true", help="Skip pipeline execution")
    return parser


def discover_suite_stories(suite_arg: str) -> tuple[List[Path], str, str]:
    """Discover all story paths in a suite and extract metadata."""
    suite_path = Path(suite_arg)
    manifest_path = suite_path / "suite_manifest.yaml" if suite_path.is_dir() else suite_path
    
    if not manifest_path.exists():
        raise FileNotFoundError(f"Suite manifest not found: {manifest_path}")
    
    manifest = load_suite_manifest(manifest_path)
    base = manifest_path.parent
    story_paths = [base / item for item in manifest.stories]
    return story_paths, manifest.dataset_id, manifest.suite_tier


def sample_acs_by_tier(
    story: EvalStory, seed: int, run_index: int
) -> Dict[SuiteTier, AcceptanceCriterion]:
    """Deterministically sample one AC per tier from a story."""
    # Use SHA256 for deterministic, stable hashing across Python processes
    story_hash = int(hashlib.sha256(story.story_id.encode('utf-8')).hexdigest(), 16)
    rng = random.Random(seed + run_index + story_hash)
    
    by_tier: Dict[SuiteTier, List[AcceptanceCriterion]] = {
        "easy": [],
        "medium": [],
        "hard": [],
    }
    
    for ac in story.acceptance_criteria:
        by_tier[ac.tier].append(ac)
    
    sampled: Dict[SuiteTier, AcceptanceCriterion] = {}
    for tier, acs in by_tier.items():
        if acs:
            sampled[tier] = rng.choice(acs)
    
    return sampled


def execute_ac_check(
    ac: AcceptanceCriterion,
    agent_output: str,
    repo_path: Optional[Path] = None,
) -> CheckResult:
    """Execute a single AC's check and return the result."""
    if ac.check is None:
        # No check defined - treat as attempted but failed
        return CheckResult(
            check_id=ac.ac_id,
            passed=False,
            attempted=True,
            failure_reason="ASSERTION_MISS",
            message="No check definition for AC",
        )
    
    return run_check(ac.check, agent_output=agent_output, repo_path=repo_path)


def compute_tier_pass_rates(ac_results: List[ACRunResult]) -> Dict[str, float]:
    """Compute aggregate pass rates per tier using existing scoring primitives.
    
    Converts ACRunResult to CheckResult format and leverages score_for_difficulty
    from eval/scoring.py to calculate tier pass rates.
    """
    # Convert ACRunResult to CheckResult format for each run
    # We expand each ACRunResult with multiple runs into individual CheckResults
    check_results: List[CheckResult] = []
    
    for ac_result in ac_results:
        # Map SuiteTier to Difficulty for scoring.py compatibility
        # easy -> low, medium -> medium, hard -> high
        tier_to_difficulty = {
            "easy": "low",
            "medium": "medium",
            "hard": "high",
        }
        difficulty = tier_to_difficulty[ac_result.tier]
        
        # Create individual CheckResults for each run
        for run_idx in range(ac_result.runs_total):
            passed = run_idx < ac_result.runs_passed
            check_results.append(
                CheckResult(
                    check_id=f"{ac_result.ac_id}_run_{run_idx}",
                    passed=passed,
                    attempted=True,
                    difficulty=difficulty,
                )
            )
    
    # Use existing scoring primitives to calculate pass rates per tier
    return {
        "easy": score_for_difficulty(check_results, "low"),
        "medium": score_for_difficulty(check_results, "medium"),
        "hard": score_for_difficulty(check_results, "high"),
    }


def classify_calibration(tier_pass_rates: Dict[str, float]) -> CalibrationClassification:
    """Classify calibration using pure Python thresholds (no LLM)."""
    easy = tier_pass_rates.get("easy", 0.0)
    medium = tier_pass_rates.get("medium", 0.0)
    hard = tier_pass_rates.get("hard", 0.0)
    
    # Priority order matters: check most specific conditions first
    if easy < 0.50:
        return "overall_too_hard"
    if hard >= 0.75:
        return "overall_too_easy"
    if medium < 0.35 or medium > 0.74:
        return "medium_miscalibrated"
    if easy >= 0.75 and 0.35 <= medium <= 0.74 and hard < 0.50:
        return "well_calibrated"
    
    # Default fallback if thresholds don't cleanly categorize
    return "medium_miscalibrated"


def generate_suggestions(
    classification: CalibrationClassification,
    tier_pass_rates: Dict[str, float],
    ac_results: List[ACRunResult],
    runner: Optional[str] = None,
    model: Optional[str] = None,
    repo_path: Optional[Path] = None,
) -> List[str]:
    """Generate narrative suggestions for recalibration.
    
    If runner/repo are omitted, produces deterministic empty suggestions.
    This is fakeable in tests by omitting runner/repo.
    """
    if runner is None or repo_path is None:
        # Deterministic fallback when no LLM is available
        return [
            f"Classification: {classification}",
            f"Tier pass rates - Easy: {tier_pass_rates.get('easy', 0.0):.2f}, "
            f"Medium: {tier_pass_rates.get('medium', 0.0):.2f}, "
            f"Hard: {tier_pass_rates.get('hard', 0.0):.2f}",
            "Consider reviewing ACs with pass rates furthest from target ranges.",
        ]
    
    # Generate LLM-based suggestions using the configured runner
    prompt = _build_suggestion_prompt(classification, tier_pass_rates, ac_results)
    
    try:
        suggestion_text = run_agent_cmd(
            runner=runner,
            prompt=prompt,
            agent="calibration-advisor",
            runner_model=model,
        )
        return [line.strip() for line in suggestion_text.strip().split("\n") if line.strip()]
    except Exception as exc:
        # Graceful degradation if LLM call fails
        return [
            f"Could not generate LLM suggestions: {exc}",
            f"Classification: {classification}",
            "Manual review recommended.",
        ]


def _build_suggestion_prompt(
    classification: CalibrationClassification,
    tier_pass_rates: Dict[str, float],
    ac_results: List[ACRunResult],
) -> str:
    """Build prompt for LLM-based suggestion generation."""
    prompt_parts = [
        "You are a calibration advisor for an evaluation suite.",
        f"\nClassification: {classification}",
        f"\nTier pass rates:",
        f"  Easy: {tier_pass_rates.get('easy', 0.0):.2%}",
        f"  Medium: {tier_pass_rates.get('medium', 0.0):.2%}",
        f"  Hard: {tier_pass_rates.get('hard', 0.0):.2%}",
        f"\nTarget ranges:",
        f"  Easy: ≥75%",
        f"  Medium: 35-74%",
        f"  Hard: <50%",
        f"\nAC results (sorted by pass rate deviation):",
    ]
    
    # Sort ACs by deviation from target ranges
    sorted_acs = sorted(ac_results, key=lambda ac: _deviation_from_target(ac))
    for ac in sorted_acs[:10]:  # Top 10 most problematic
        prompt_parts.append(
            f"  - {ac.ac_id} ({ac.tier}): {ac.pass_rate:.2%} "
            f"({ac.runs_passed}/{ac.runs_total} runs)"
        )
    
    prompt_parts.extend([
        "\nProvide 3-5 concrete suggestions for recalibrating ACs.",
        "Focus on the most problematic ACs and suggest specific rewrites or adjustments.",
    ])
    
    return "\n".join(prompt_parts)


def _deviation_from_target(ac: ACRunResult) -> float:
    """Compute deviation from target pass rate range."""
    targets = {
        "easy": (0.75, 1.0),
        "medium": (0.35, 0.74),
        "hard": (0.0, 0.50),
    }
    low, high = targets[ac.tier]
    
    if ac.pass_rate < low:
        return low - ac.pass_rate
    if ac.pass_rate > high:
        return ac.pass_rate - high
    return 0.0


def run_calibration_validation(
    suite_path: str,
    runs: int,
    seed: int,
    output_path: str,
    repo: Optional[str] = None,
    runner: Optional[str] = None,
    model: Optional[str] = None,
    skip_opik: bool = False,
    skip_pipeline: bool = False,
) -> CalibrationReport:
    """Execute calibration validation and return the report."""
    story_paths, dataset_id, suite_tier = discover_suite_stories(suite_path)
    
    if not story_paths:
        raise ValueError(f"No stories found in suite: {suite_path}")
    
    # Load all stories
    stories = [load_eval_story(path) for path in story_paths]
    
    # Track results per AC across all runs
    ac_run_tracker: Dict[str, Dict[str, Any]] = {}
    
    # Execute sampling runs
    for run_idx in range(runs):
        for story in stories:
            sampled_acs = sample_acs_by_tier(story, seed, run_idx)
            
            for tier, ac in sampled_acs.items():
                if ac.ac_id not in ac_run_tracker:
                    ac_run_tracker[ac.ac_id] = {
                        "tier": tier,
                        "text": ac.text,
                        "runs_passed": 0,
                        "runs_total": 0,
                    }
                
                # Execute check with fake agent output for validation
                # In a real scenario, this would use run_eval.py or actual workflow execution
                fake_output = f"Mock agent output for {story.story_id} AC {ac.ac_id}"
                result = execute_ac_check(ac, fake_output, Path(repo) if repo else None)
                
                ac_run_tracker[ac.ac_id]["runs_total"] += 1
                if result.passed:
                    ac_run_tracker[ac.ac_id]["runs_passed"] += 1
    
    # Build AC results list
    ac_results = [
        ACRunResult(
            ac_id=ac_id,
            tier=data["tier"],
            text=data["text"],
            runs_passed=data["runs_passed"],
            runs_total=data["runs_total"],
            pass_rate=data["runs_passed"] / data["runs_total"] if data["runs_total"] > 0 else 0.0,
        )
        for ac_id, data in ac_run_tracker.items()
    ]
    
    # Compute tier pass rates
    tier_pass_rates = compute_tier_pass_rates(ac_results)
    
    # Classify calibration
    classification = classify_calibration(tier_pass_rates)
    recalibration_needed = classification != "well_calibrated"
    
    # Generate suggestions
    suggestions = generate_suggestions(
        classification,
        tier_pass_rates,
        ac_results,
        runner=runner,
        model=model,
        repo_path=Path(repo) if repo else None,
    )
    
    # Build report
    report = CalibrationReport(
        suite_path=suite_path,
        dataset_id=dataset_id,
        calibrated_at=datetime.now(timezone.utc).isoformat(),
        runs=runs,
        seed=seed,
        tier_pass_rates=tier_pass_rates,
        ac_results=[
            {
                "ac_id": ac.ac_id,
                "tier": ac.tier,
                "text": ac.text,
                "runs_passed": ac.runs_passed,
                "runs_total": ac.runs_total,
                "pass_rate": ac.pass_rate,
            }
            for ac in ac_results
        ],
        classification=classification,
        recalibration_needed=recalibration_needed,
        suggestions=suggestions,
        apply_instructions=(
            "Do NOT auto-apply suggestions. Manual review and re-synthesis required."
        ),
    )
    
    return report


def write_report(report: CalibrationReport, output_path: str) -> None:
    """Write calibration report to JSON file."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    
    with output.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "suite_path": report.suite_path,
                "dataset_id": report.dataset_id,
                "calibrated_at": report.calibrated_at,
                "runs": report.runs,
                "seed": report.seed,
                "tier_pass_rates": report.tier_pass_rates,
                "ac_results": report.ac_results,
                "classification": report.classification,
                "recalibration_needed": report.recalibration_needed,
                "suggestions": report.suggestions,
                "apply_instructions": report.apply_instructions,
            },
            f,
            indent=2,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main CLI entry point."""
    args = build_parser().parse_args(argv)
    
    try:
        report = run_calibration_validation(
            suite_path=args.suite,
            runs=args.runs,
            seed=args.seed,
            output_path=args.output,
            repo=args.repo,
            runner=args.runner,
            model=args.model,
            skip_opik=args.skip_opik,
            skip_pipeline=args.skip_pipeline,
        )
        
        write_report(report, args.output)
        
        print(f"Calibration report written to: {args.output}")
        print(f"Classification: {report.classification}")
        print(f"Recalibration needed: {report.recalibration_needed}")
        print(f"\nTier pass rates:")
        for tier, rate in report.tier_pass_rates.items():
            print(f"  {tier}: {rate:.2%}")
        
        return 0 if not report.recalibration_needed else 1
    
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
