"""Opik-compatible metric conversion for evaluation results."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from .models import CheckResult, ScoreSummary
from .scoring import summary_as_metric_values

try:  # pragma: no cover - exercised when the optional Opik package is present.
    from _opik import ScoreResult as OpikScoreResult
except Exception:  # pragma: no cover - fallback is covered by unit tests.
    OpikScoreResult = None


@dataclass(frozen=True)
class LocalScoreResult:
    """Small ScoreResult-compatible fallback used when Opik is unavailable."""

    name: str
    value: float
    reason: str | None = None
    category_name: str | None = None
    metadata: dict[str, Any] | None = None
    scoring_failed: bool = False


ScoreResultType = OpikScoreResult or LocalScoreResult


def score_result(
    *,
    name: str,
    value: float,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    scoring_failed: bool = False,
):
    return ScoreResultType(
        name=name,
        value=value,
        reason=reason,
        metadata=metadata or {},
        scoring_failed=scoring_failed,
    )


def check_score_results(
    check_results: Iterable[CheckResult],
    *,
    suite_tier: str | None = None,
    regression: bool = False,
) -> list[Any]:
    """Convert individual check results to ScoreResult-compatible objects.

    Metric names intentionally use only ``{check_id}_{check_subject}``; difficulty
    and other structured fields are recorded in metadata.
    """

    converted = []
    for result in check_results:
        subject = result.subject or "agent_output"
        metadata = {
            **dict(result.metadata),
            "difficulty": result.difficulty,
            "mechanism": result.mechanism,
            "failure": result.failure_reason,
            "suite_tier": suite_tier,
            "regression": regression,
        }
        converted.append(
            score_result(
                name=f"{result.check_id}_{subject}",
                value=1.0 if result.passed else 0.0,
                reason=result.message or None,
                metadata=metadata,
                scoring_failed=False,
            )
        )
    return converted


def summary_score_results(
    summary: ScoreSummary,
    *,
    suite_tier: str | None = None,
    regression: bool = False,
) -> list[Any]:
    metadata = {**dict(summary.metadata), "suite_tier": suite_tier, "regression": regression}
    return [
        score_result(name=name, value=value, metadata=metadata)
        for name, value in summary_as_metric_values(summary).items()
    ]


def eval_score_results(
    check_results: Iterable[CheckResult],
    summary: ScoreSummary,
    *,
    suite_tier: str | None = None,
    regression: bool = False,
) -> list[Any]:
    return [
        *check_score_results(check_results, suite_tier=suite_tier, regression=regression),
        *summary_score_results(summary, suite_tier=suite_tier, regression=regression),
    ]


class EvalScoreMetric:
    """Opik BaseMetric-like adapter returning precomputed ScoreResults."""

    def __init__(self, results: Sequence[Any], name: str = "agent_workbench_eval") -> None:
        self.name = name
        self.results = list(results)

    def score(self, *args: Any, **kwargs: Any) -> list[Any]:
        return list(self.results)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert eval results to Opik-compatible metrics.")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
