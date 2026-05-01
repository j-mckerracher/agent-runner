"""Public plugin API contract for evaluation checks."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .models import CheckDefinition, EvalStory

PLUGIN_API_VERSION = "1.0"


@runtime_checkable
class CheckPlugin(Protocol):
    api_version: str

    def validate(self) -> None:
        """Raise an exception when plugin configuration is invalid."""

    def get_checks(self, story: EvalStory) -> Sequence[CheckDefinition]:
        """Return additional checks for the supplied eval story."""
