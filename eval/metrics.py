"""
Custom Opik metrics for agent-runner pipeline evaluation.

VerificationCheckMetric  — runs story-specific eval checks and emits one
ScoreResult per check plus a composite pipeline_score (0.0–1.0).
"""
from __future__ import annotations

from pathlib import Path

from opik.evaluation.metrics import base_metric, score_result

from eval.story_checks import run_story_checks


class VerificationCheckMetric(base_metric.BaseMetric):
    """
    Runs story-specific checks against the target Angular monorepo and returns
    one ScoreResult per verification check, plus a composite pipeline_score.

    Args:
        mono_root: Absolute path to the mcs-products-mono-ui repository.
        change_id: Evaluation story id to score.
        story_file: Optional path to a story JSON fixture.
        name: Metric name used in Opik (default: "verification_checks").
        timeout: Max seconds to wait per command check.
    """

    def __init__(
        self,
        mono_root: str,
        change_id: str | None = None,
        story_file: str | None = None,
        name: str = "verification_checks",
        timeout: int = 600,
    ):
        super().__init__(name)
        self.mono_root = str(Path(mono_root).resolve())
        self.change_id = change_id
        self.story_file = story_file
        self.timeout = timeout

    def score(
        self,
        output: str = "",
        **kwargs,
    ) -> list[score_result.ScoreResult]:
        """
        Execute story-specific checks and convert the result into ScoreResults.

        The ``output`` parameter is unused — the metric always re-runs checks
        against the live filesystem so the score reflects the actual repo state.
        """
        try:
            data = run_story_checks(
                mono_root=self.mono_root,
                change_id=self.change_id,
                story_file=self.story_file,
                timeout=self.timeout,
            )
        except ValueError as exc:
            return [score_result.ScoreResult(
                value=0.0,
                name=self.name,
                reason=f"Failed to run story checks: {exc}",
            )]

        results: list[score_result.ScoreResult] = []

        for check in data.get("checks", []):
            check_id = check["id"]
            check_name = check["name"]
            passed = check["passed"]
            results.append(score_result.ScoreResult(
                value=1.0 if passed else 0.0,
                name=f"check_{check_id:02d}_{check_name}",
                reason="PASS" if passed else "FAIL",
            ))

        passing = data.get("passing", 0)
        total = data.get("total", 1)
        score = data.get("score", 0)
        results.append(score_result.ScoreResult(
            value=score / 100.0,
            name="pipeline_score",
            reason=f"{passing}/{total} checks passed → {score}/100",
        ))

        return results
