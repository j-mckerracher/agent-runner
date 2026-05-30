from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable

from .runner_models import KNOWN_RUNNERS, resolve_runner_cheapest_model


@dataclass(frozen=True)
class RunnerFailoverCandidate:
    runner: str
    model: str


def _runner_key(runner: str | None) -> str:
    return (runner or "").strip().lower()


def _valid_runners(config: dict | None) -> set[str]:
    aliases = (config or {}).get("runner_aliases") or {}
    alias_names = aliases.keys() if isinstance(aliases, dict) else ()
    return {_runner_key(runner) for runner in (*KNOWN_RUNNERS, *alias_names)}


def _job_override_runners(raw_overrides: object) -> list[str]:
    if raw_overrides is None:
        return []
    overrides = raw_overrides
    if isinstance(raw_overrides, str):
        if not raw_overrides.strip():
            return []
        try:
            overrides = json.loads(raw_overrides)
        except json.JSONDecodeError:
            return []
    if not isinstance(overrides, dict):
        return []

    runners: list[str] = []
    for override in overrides.values():
        if not isinstance(override, dict):
            continue
        runner = override.get("runner")
        if isinstance(runner, str) and runner.strip():
            runners.append(runner.strip())
    return runners


def _job_runners(job: dict[str, Any]) -> list[str]:
    runners: list[str] = []
    runner = job.get("runner")
    if isinstance(runner, str) and runner.strip():
        runners.append(runner.strip())
    runners.extend(_job_override_runners(job.get("agent_llm_overrides")))
    return runners


def discover_prior_runner_candidates(
    jobs: Iterable[dict[str, Any]],
    *,
    config: dict | None,
    current_runner: str | None = None,
) -> list[RunnerFailoverCandidate]:
    """Return eligible prior runners in most-recent job-history order."""
    valid = _valid_runners(config)
    current_key = _runner_key(current_runner)
    seen: set[str] = set()
    candidates: list[RunnerFailoverCandidate] = []

    for job in jobs:
        if not isinstance(job, dict):
            continue
        for runner in _job_runners(job):
            key = _runner_key(runner)
            if not key or key in seen or key == current_key or key not in valid:
                continue
            try:
                model = resolve_runner_cheapest_model(runner, config=config)
            except ValueError:
                continue
            seen.add(key)
            candidates.append(RunnerFailoverCandidate(runner=runner, model=model))

    return candidates


_USAGE_EXHAUSTION_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota has been exceeded",
    "exceeded your quota",
    "usage limit reached",
    "usage limit has been reached",
    "premium request",
    "premium requests",
    "monthly limit",
    "monthly usage limit",
    "monthly request limit",
    "ran out",
    "out of credits",
    "credits exhausted",
    "no credits",
    "allowance exhausted",
    "allowance has been exhausted",
    "billing hard limit",
    "hard limit",
    "resource_exhausted",
    "resource exhausted",
    "upgrade your plan",
    "upgrade to continue",
    "payment required",
)


def usage_exhaustion_reason(text: str) -> str | None:
    normalized = (text or "").lower()
    if not normalized:
        return None
    if "rate limit" in normalized and not any(
        marker in normalized
        for marker in (
            "quota",
            "premium request",
            "monthly",
            "allowance",
            "billing",
            "resource_exhausted",
            "resource exhausted",
        )
    ):
        return None
    for marker in _USAGE_EXHAUSTION_MARKERS:
        if marker in normalized:
            return marker
    return None


def is_usage_exhaustion_error_text(text: str) -> bool:
    return usage_exhaustion_reason(text) is not None


def usage_exhaustion_error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    if isinstance(exc, subprocess.CalledProcessError):
        for value in (exc.output, exc.stdout, exc.stderr):
            if value:
                parts.append(str(value))
    return "\n".join(parts)


def is_usage_exhaustion_exception(exc: BaseException) -> bool:
    return is_usage_exhaustion_error_text(usage_exhaustion_error_text(exc))


class RunnerFailoverPolicy:
    def __init__(self, candidates: Iterable[RunnerFailoverCandidate] = ()):
        self._candidates = tuple(candidates)
        self._exhausted: dict[str, dict[str, str | None]] = {}

    @property
    def candidates(self) -> tuple[RunnerFailoverCandidate, ...]:
        return self._candidates

    def mark_exhausted(self, runner: str, *, model: str | None, reason: str | None = None) -> None:
        self._exhausted[_runner_key(runner)] = {"runner": runner, "model": model, "reason": reason}

    def is_exhausted(self, runner: str) -> bool:
        return _runner_key(runner) in self._exhausted

    def next_candidate(self, *, excluding: Iterable[str] = ()) -> RunnerFailoverCandidate | None:
        excluded = {_runner_key(runner) for runner in excluding}
        excluded.update(self._exhausted)
        for candidate in self._candidates:
            if _runner_key(candidate.runner) not in excluded:
                return candidate
        return None
