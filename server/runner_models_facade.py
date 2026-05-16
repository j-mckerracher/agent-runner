"""Thin facade so server.routes don't need to know about runner_models layout."""
from __future__ import annotations

from core.runner_models import (
    KNOWN_RUNNERS,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
)


def runner_choices(config: dict | None = None) -> dict:
    """Return runner choices: known providers + custom aliases from config."""
    runner_aliases = (config or {}).get("runner_aliases", {})
    all_runners = list(KNOWN_RUNNERS) + list(runner_aliases.keys())
    models: dict[str, list[str]] = {k: list(v) for k, v in RUNNER_MODEL_CHOICES.items()}
    # Custom aliases carry exactly one implied model
    for alias_name, alias_def in runner_aliases.items():
        provider = alias_def.get("provider", "")
        model = alias_def.get("model", "")
        models[alias_name] = [f"{provider}/{model}" if provider and model else model or provider]
    return {
        "runners": all_runners,
        "models": models,
        "defaults": dict(RUNNER_DEFAULT_MODELS),
    }
