"""Weighted score primitives for evaluation check results."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, Mapping, Sequence

from .models import CheckDefinition, CheckResult, Difficulty, ScoreSummary

DEFAULT_DIFFICULTY_WEIGHTS: Mapping[Difficulty, float] = {
    "low": 1.0,
    "medium": 2.0,
    "high": 3.0,
}

HARD_SUBJECT_SIGNALS = {"build", "compile", "lint", "execute", "ground-truth"}
LOW_SUBJECT_SIGNALS = {"file", "string", "key-term"}


def suggested_difficulty_for_check(definition: CheckDefinition) -> Difficulty:
    """Assign difficulty from mechanism and subject signals.

    Mechanism signal: command +1, matches 0, contains -1.
    Subject signal: build/compile/lint/execute/ground-truth +1;
    file/string/key-term -1; all other subjects 0.
    """

    mechanism_score = {"command": 1, "matches": 0, "contains": -1}[definition.mechanism]
    subject_score = _subject_signal(definition.subject)
    total = mechanism_score + subject_score
    if total >= 1:
        return "high"
    if total == 0:
        return "medium"
    return "low"


def assign_check_difficulty(
    definition: CheckDefinition,
    difficulty_overrides: Mapping[str, Difficulty] | None = None,
) -> CheckDefinition:
    """Return a definition with effective and suggested difficulty populated.

    Manual difficulty (either on the definition or supplied in overrides by check
    id) wins. The two-signal difficulty is still preserved in
    ``suggested_difficulty`` so callers can inspect what the automatic assignment
    would have been.
    """

    suggested = suggested_difficulty_for_check(definition)
    overrides = difficulty_overrides or {}
    manual = overrides.get(definition.id) or definition.difficulty
    effective = manual or suggested
    preserved_suggested = definition.suggested_difficulty or suggested
    if definition.difficulty == effective and definition.suggested_difficulty == preserved_suggested:
        return definition
    return replace(definition, difficulty=effective, suggested_difficulty=preserved_suggested)


def assign_difficulties(
    definitions: Iterable[CheckDefinition],
    difficulty_overrides: Mapping[str, Difficulty] | None = None,
) -> list[CheckDefinition]:
    return [assign_check_difficulty(definition, difficulty_overrides) for definition in definitions]


def _subject_signal(subject: str) -> int:
    normalized = subject.lower().replace("_", "-")
    if normalized in HARD_SUBJECT_SIGNALS:
        return 1
    if normalized in LOW_SUBJECT_SIGNALS:
        return -1
    return 0


def score_for_difficulty(results: Sequence[CheckResult], difficulty: Difficulty) -> float:
    relevant = [result for result in results if (result.difficulty or "medium") == difficulty]
    if not relevant:
        return 0.0
    passed = sum(1 for result in relevant if result.passed)
    return passed / len(relevant)


def score_tier_low(results: Sequence[CheckResult]) -> float:
    return score_for_difficulty(results, "low")


def score_tier_medium(results: Sequence[CheckResult]) -> float:
    return score_for_difficulty(results, "medium")


def score_tier_high(results: Sequence[CheckResult]) -> float:
    return score_for_difficulty(results, "high")


def weighted_composite(
    results: Sequence[CheckResult],
    weights: Mapping[Difficulty, float] = DEFAULT_DIFFICULTY_WEIGHTS,
) -> float:
    weighted_total = 0.0
    total_weight = 0.0
    for result in results:
        difficulty = result.difficulty or "medium"
        weight = weights[difficulty]
        weighted_total += weight if result.passed else 0.0
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return weighted_total / total_weight


def score_weighted_composite(
    results: Sequence[CheckResult],
    weights: Mapping[Difficulty, float] = DEFAULT_DIFFICULTY_WEIGHTS,
) -> float:
    return weighted_composite(results, weights)


def score_attempted_rate(results: Sequence[CheckResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result.attempted) / len(results)


def summarize_scores(
    results: Iterable[CheckResult],
    weights: Mapping[Difficulty, float] = DEFAULT_DIFFICULTY_WEIGHTS,
) -> ScoreSummary:
    result_list = list(results)
    total = len(result_list)
    passed = sum(1 for result in result_list if result.passed)
    attempted = sum(1 for result in result_list if result.attempted)
    attempted_rate = attempted / total if total else 0.0
    return ScoreSummary(
        total_checks=total,
        passed_checks=passed,
        attempted_checks=attempted,
        score_tier_low=score_tier_low(result_list),
        score_tier_medium=score_tier_medium(result_list),
        score_tier_high=score_tier_high(result_list),
        weighted_composite=score_weighted_composite(result_list, weights),
        attempted_rate=attempted_rate,
        metadata={"weights": dict(weights)},
    )


def summary_as_metric_values(summary: ScoreSummary) -> Dict[str, float]:
    return {
        "score_tier_low": summary.score_tier_low,
        "score_tier_medium": summary.score_tier_medium,
        "score_tier_high": summary.score_tier_high,
        "score_weighted_composite": summary.weighted_composite,
        "score_attempted_rate": summary.attempted_rate,
    }
