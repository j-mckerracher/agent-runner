"""Story-level acceptance check collection and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from .check_helpers import run_check
from .models import AcceptanceCriterion, CheckDefinition, CheckResult, Difficulty, EvalStory
from .plugin_loader import load_plugin, plugin_checks
from .scoring import assign_difficulties

PathLike = Union[str, Path]

_DECLINE_MARKERS = (
    "i cannot",
    "i can't",
    "cannot complete",
    "can't complete",
    "unable to complete",
    "i must decline",
    "i decline this request",
    "i decline the request",
    "i decline to complete",
    "i decline to answer",
    "i refuse",
)

_IMPLEMENTATION_MARKERS = (
    "implemented",
    "changed",
    "updated",
    "added",
    "modified",
    "created",
    "fixed",
    "files changed",
    "```",
)

_DECLINE_PREFIXES = (
    "unfortunately, ",
    "unfortunately ",
    "apologies, but ",
    "apologies but ",
    "apologies, ",
    "apologies ",
    "sorry, but ",
    "sorry but ",
    "sorry, ",
    "sorry ",
    "i'm sorry, but ",
    "i'm sorry but ",
    "i'm sorry, ",
    "i'm sorry ",
    "i am sorry, but ",
    "i am sorry but ",
    "i am sorry, ",
    "i am sorry ",
)


def get_story_checks(
    story_id: str,
    *,
    plugin: object = None,
    difficulty_overrides: Optional[Mapping[str, Difficulty]] = None,
    suite_story: Optional[Union[EvalStory, Mapping[str, object]]] = None,
) -> Sequence[CheckDefinition]:
    """Return built-in plus plugin checks for a story.

    Built-in checks are read from ``suite_story.acceptance_criteria[].check``.
    If a plugin is supplied, it may be an already-loaded plugin object or a path
    to a plugin module.
    """

    story = _coerce_story(story_id, suite_story)
    checks = _checks_from_story(story)
    plugin_obj = load_plugin(plugin) if isinstance(plugin, (str, Path)) else plugin
    if plugin_obj is not None:
        checks.extend(plugin_checks(plugin_obj, story, built_in_checks=checks))
    _validate_unique_ids(checks)
    return assign_difficulties(checks, difficulty_overrides)


def run_story_checks(
    story_id: str,
    agent_output: str,
    *,
    plugin: object = None,
    difficulty_overrides: Optional[Mapping[str, Difficulty]] = None,
    suite_story: Optional[Union[EvalStory, Mapping[str, object]]] = None,
    repo_path: Optional[PathLike] = None,
    timeout: int = 30,
) -> list[CheckResult]:
    """Execute all checks for ``story_id`` and return per-check results."""

    checks = get_story_checks(
        story_id,
        plugin=plugin,
        difficulty_overrides=difficulty_overrides,
        suite_story=suite_story,
    )
    if _is_no_attempt(agent_output):
        return [
            CheckResult(
                check_id=check.id,
                passed=False,
                attempted=False,
                mechanism=check.mechanism,
                subject=check.subject,
                difficulty=check.difficulty,
                failure_reason="NO_ATTEMPT",
                message="No substantive agent output",
                metadata={"label": check.label, **dict(check.metadata)},
            )
            for check in checks
        ]

    results: list[CheckResult] = []
    for check in checks:
        result = run_check(check, agent_output=agent_output, repo_path=repo_path, timeout_seconds=timeout)
        if not result.passed and result.failure_reason is None:
            result = CheckResult(
                check_id=result.check_id,
                passed=False,
                attempted=result.attempted,
                mechanism=result.mechanism,
                subject=result.subject,
                difficulty=result.difficulty,
                failure_reason="ASSERTION_MISS",
                message=result.message,
                metadata=result.metadata,
            )
        results.append(result)
    return results


def _coerce_story(
    story_id: str,
    suite_story: Optional[Union[EvalStory, Mapping[str, object]]],
) -> EvalStory:
    if suite_story is None:
        return EvalStory(story_id=story_id, title=story_id, description="Story checks")
    story = suite_story if isinstance(suite_story, EvalStory) else EvalStory.from_dict(suite_story)
    if story.story_id != story_id:
        raise ValueError(f"suite_story id {story.story_id!r} does not match requested story_id {story_id!r}")
    return story


def _checks_from_story(story: EvalStory) -> list[CheckDefinition]:
    checks: list[CheckDefinition] = []
    for criterion in story.acceptance_criteria:
        if isinstance(criterion, AcceptanceCriterion):
            accepted = criterion
        elif isinstance(criterion, Mapping):
            accepted = AcceptanceCriterion.from_dict(criterion)
        else:
            raise ValueError(f"Unsupported acceptance criterion type: {type(criterion).__name__}")
        if accepted.check is not None:
            checks.append(accepted.check)
    return checks


def _validate_unique_ids(checks: Sequence[CheckDefinition]) -> None:
    seen: set[str] = set()
    for check in checks:
        if check.id in seen:
            raise ValueError(f"Duplicate check id: {check.id}")
        seen.add(check.id)


def _is_no_attempt(agent_output: str) -> bool:
    if not isinstance(agent_output, str) or not agent_output.strip():
        return True
    normalized = " ".join(agent_output.lower().split())
    normalized = " ".join(normalized.replace(",", ", ").split())
    if any(marker in normalized for marker in _IMPLEMENTATION_MARKERS):
        return False
    normalized = _strip_decline_prefix(normalized)
    return any(normalized.startswith(marker) for marker in _DECLINE_MARKERS)


def _strip_decline_prefix(normalized: str) -> str:
    for prefix in _DECLINE_PREFIXES:
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run eval story checks for one story.")
    parser.add_argument("story_id")
    parser.add_argument("--agent-output", default="")
    parser.add_argument("--plugin")
    args = parser.parse_args(argv)
    results = run_story_checks(args.story_id, args.agent_output, plugin=args.plugin)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
