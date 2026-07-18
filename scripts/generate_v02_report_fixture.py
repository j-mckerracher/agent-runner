#!/usr/bin/env python3
"""Generate a deterministic v0.2 evaluation report fixture.

Builds a schema-valid `EvalReport` (see `eval/report_schema.py`) from a
canned set of `run_one`-shaped trial dicts, the same shape `eval/runner.py`
produces for a live run, but with no wall-clock or random-uuid inputs: every
timestamp/id fed into `eval.live_report.build_eval_report` is a literal
constant, so re-running this script always produces byte-identical output.

This exists so `eval/comparison.py` (round-trip: does `compare_reports`
accept a report built via the real `build_eval_report` path?) and any
downstream reader/adapter has a stable, non-hand-authored v0.2 fixture to
exercise against, distinct from the hand-authored schema fixture at
`tests/fixtures/eval/v02_report.json` (which exists purely to pin the
schema's on-disk shape, not to model a realistic multi-trial run).

Usage:
    python3 scripts/generate_v02_report_fixture.py
    python3 scripts/generate_v02_report_fixture.py --output out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.live_report import build_eval_report  # noqa: E402
from eval.report_schema import validate_report_payload  # noqa: E402

CREATED_AT = "2026-07-18T00:00:00Z"
EVAL_RUN_ID = "run-fixture-generated-001"

# Fixed args namespace: no CLI/env dependency, so the resulting report's
# `candidate_version`/`baseline_version`/`metadata` are stable across runs.
FIXTURE_ARGS = SimpleNamespace(
    sha="fixturesha0001",
    runner="claude",
    model="fixture-model",
    repo="/repo",
    difficulty="mixed",
    compare_to="fixturesha0000",
)

_STORY_EASY = {
    "acceptance_criteria": ["AC1: First behavior.", "AC2: Second behavior."],
    "metadata": {"critical_acceptance_criteria": ["AC1"], "domain": "fixture"},
}

_STORY_MEDIUM = {
    "acceptance_criteria": ["AC1: Renders correctly.", "AC2: Handles edge case."],
    "metadata": {"critical_acceptance_criteria": ["AC1", "AC2"], "domain": "fixture"},
}


def _trial(
    *,
    benchmark_id: str,
    difficulty: str,
    run_id: str,
    trial_index: int,
    status: str,
    error: str | None,
    wall_seconds: float,
    score_weighted: float,
    story: dict[str, Any],
    ac_results: dict[str, Any] | None,
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "difficulty": difficulty,
        "run_id": run_id,
        "trial_index": trial_index,
        "status": status,
        "error": error,
        "started_at": started_at,
        "completed_at": completed_at,
        "score_weighted": score_weighted,
        "metrics": {"wall_seconds": wall_seconds},
        "story": story,
        "hidden_tests": (
            {"total": len(ac_results), "passed": sum(1 for v in ac_results.values() if v.get("passed")), "ac_results": ac_results}
            if ac_results is not None
            else None
        ),
        "evidence": {
            "story_ref": f"evidence/{EVAL_RUN_ID}/{benchmark_id}/{run_id}/story.json",
            "workflow_stdout_ref": f"evidence/{EVAL_RUN_ID}/{benchmark_id}/{run_id}/workflow.stdout.log",
            "trace_ref": f"evidence/{EVAL_RUN_ID}/{benchmark_id}/{run_id}/trace.jsonl",
            **({"hidden_tests_xml_ref": f"evidence/{EVAL_RUN_ID}/{benchmark_id}/{run_id}/hidden_tests.xml"} if ac_results else {}),
        },
    }


def build_canned_trials() -> list[dict[str, Any]]:
    """Two cases (easy, medium); easy has two trials (one clean pass, one
    workflow failure -> ACs honestly unknown) to exercise PARTIAL case status
    and the ac-status-never-inferred-from-absence rule in one fixture."""
    return [
        _trial(
            benchmark_id="easy",
            difficulty="easy",
            run_id="easy-t1",
            trial_index=1,
            status="PASS",
            error=None,
            wall_seconds=8.0,
            score_weighted=1.0,
            story=_STORY_EASY,
            ac_results={
                "AC1": {"tests": ["test_ac1_first"], "passed": True},
                "AC2": {"tests": ["test_ac2_second"], "passed": True},
            },
            started_at="2026-07-18T00:00:00Z",
            completed_at="2026-07-18T00:00:08Z",
        ),
        _trial(
            benchmark_id="easy",
            difficulty="easy",
            run_id="easy-t2",
            trial_index=2,
            status="FAIL",
            error="workflow failed",
            wall_seconds=2.5,
            score_weighted=0.0,
            story=_STORY_EASY,
            ac_results=None,  # workflow failed before hidden tests ran -> unknown, not fail
            started_at="2026-07-18T00:00:10Z",
            completed_at="2026-07-18T00:00:12Z",
        ),
        _trial(
            benchmark_id="medium",
            difficulty="medium",
            run_id="medium-t1",
            trial_index=1,
            status="FAIL",
            error="hidden tests failed",
            wall_seconds=15.0,
            score_weighted=0.5,
            story=_STORY_MEDIUM,
            ac_results={
                "AC1": {"tests": ["test_ac1_renders"], "passed": True},
                "AC2": {"tests": ["test_ac2_edge_case"], "failed": True},
            },
            started_at="2026-07-18T00:01:00Z",
            completed_at="2026-07-18T00:01:15Z",
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "tests" / "fixtures" / "eval" / "v02_report_generated.json",
        help="Fixture output path (default: tests/fixtures/eval/v02_report_generated.json).",
    )
    args = parser.parse_args(argv)

    report = build_eval_report(
        build_canned_trials(),
        FIXTURE_ARGS,
        created_at=CREATED_AT,
        eval_run_id=EVAL_RUN_ID,
        comparison_context={
            "trend": "insufficient data",
            "warnings": ["Fixture report: not a real comparison baseline."],
        },
    )

    payload = report.to_dict()
    errors = validate_report_payload(payload)
    if errors:
        print("Generated report failed schema validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote deterministic v0.2 report fixture to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
