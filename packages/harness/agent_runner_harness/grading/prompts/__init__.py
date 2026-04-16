"""Prompt loading and rendering for the rubric judge.

PROMPT_VERSION is a semver-like integer that must be bumped whenever the
rubric_template.md or system.md content changes in a semantically meaningful
way. Bumping PROMPT_VERSION is a rebaseline event — see docs/rebaseline.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PROMPT_VERSION = "1"

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt file from this directory by name (without extension).

    Args:
        name: File stem, e.g. ``"rubric_template"`` or ``"system"``.

    Returns:
        The file contents as a string.

    Raises:
        FileNotFoundError: If no matching ``.md`` file exists.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def render_rubric_prompt(
    criterion: Any,
    task: Any,
    artifact_summary: str,
    event_log_excerpt: str,
) -> str:
    """Render the rubric evaluation prompt for a single criterion.

    Substitutes all ``{{variable}}`` placeholders using simple string
    replacement (no Jinja2 dependency).

    Args:
        criterion: An ``AcceptanceCriterion``-compatible object with at least
            ``description``, ``scale``, and ``threshold`` attributes.
        task: A ``Task``-compatible object with at least a ``title`` attribute.
            If a plain string is passed it is used as the task description.
        artifact_summary: Short human-readable list of produced artifact paths.
        event_log_excerpt: Relevant excerpt from the run event log.

    Returns:
        Rendered prompt string ready to send to the judge.
    """
    template = load_prompt("rubric_template")

    task_description = task if isinstance(task, str) else getattr(task, "title", str(task))
    criterion_description = criterion.description
    scale = str(criterion.scale or "0-3")
    threshold = str(criterion.threshold if criterion.threshold is not None else "")

    substitutions = {
        "{{task_description}}": task_description,
        "{{criterion_description}}": criterion_description,
        "{{scale}}": scale,
        "{{threshold}}": threshold,
        "{{artifact_summary}}": artifact_summary,
        "{{event_log_excerpt}}": event_log_excerpt,
    }

    result = template
    for placeholder, value in substitutions.items():
        result = result.replace(placeholder, value)
    return result
