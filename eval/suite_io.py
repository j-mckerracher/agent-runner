"""Suite and story YAML helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Union

from .models import EvalStory, SuiteManifest
from .yaml_io import dump_yaml, load_yaml_mapping

PathLike = Union[str, Path]


class SuiteIOError(ValueError):
    """Raised when a suite or story manifest is invalid."""


def load_eval_story(path: PathLike) -> EvalStory:
    try:
        return EvalStory.from_dict(load_yaml_mapping(path))
    except (KeyError, TypeError, ValueError) as exc:
        raise SuiteIOError(f"Invalid eval story manifest {path}: {exc}") from exc


def dump_eval_story(story: EvalStory, path: PathLike) -> None:
    dump_yaml(story.to_dict(), path)


def load_suite_manifest(path: PathLike) -> SuiteManifest:
    try:
        return SuiteManifest.from_dict(load_yaml_mapping(path))
    except (KeyError, TypeError, ValueError) as exc:
        raise SuiteIOError(f"Invalid suite manifest {path}: {exc}") from exc


def dump_suite_manifest(suite: SuiteManifest, path: PathLike) -> None:
    dump_yaml(suite.to_dict(), path)


def workflow_fixture_from_story(story: EvalStory) -> Dict[str, Any]:
    """Convert a suite story into the JSON shape accepted by workflow_inputs."""

    change_id = story.change_id or story.story_id
    return {
        "change_id": change_id,
        "title": story.title,
        "description": story.description,
        "acceptance_criteria": [criterion.text for criterion in story.acceptance_criteria],
        "metadata": {
            "eval_story_id": story.story_id,
            "suite_tier": story.suite_tier,
            "dataset_id": story.dataset_id,
            **dict(story.metadata),
        },
    }
