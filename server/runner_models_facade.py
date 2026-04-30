"""Thin facade so server.routes don't need to know about runner_models layout."""
from __future__ import annotations

from runner_models import (
    COPILOT_EFFORT_CHOICES,
    RUNNER_DEFAULT_MODELS,
    RUNNER_MODEL_CHOICES,
)


def runner_choices() -> dict:
    return {
        "runners": list(RUNNER_MODEL_CHOICES.keys()),
        "models": {k: list(v) for k, v in RUNNER_MODEL_CHOICES.items()},
        "defaults": dict(RUNNER_DEFAULT_MODELS),
        "copilot_effort": list(COPILOT_EFFORT_CHOICES),
    }
