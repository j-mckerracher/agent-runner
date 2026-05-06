"""Thin facade so server.routes don't need to know about runner_models layout."""
from __future__ import annotations

from runner_models import (
    COPILOT_EFFORT_CHOICES,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
    discover_copilot_aliases,
    copilot_alias_model,
)


def runner_choices() -> dict:
    aliases = discover_copilot_aliases()
    all_runners = list(RUNNER_MODEL_CHOICES.keys()) + aliases
    models: dict[str, list[str]] = {k: list(v) for k, v in RUNNER_MODEL_CHOICES.items()}
    # Each copilot alias carries exactly one implied model (the alias suffix)
    for alias_runner in aliases:
        models[alias_runner] = [copilot_alias_model(alias_runner)]
    return {
        "runners": all_runners,
        "models": models,
        "defaults": dict(RUNNER_DEFAULT_MODELS),
        "copilot_effort": list(COPILOT_EFFORT_CHOICES),
    }
