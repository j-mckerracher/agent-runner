from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TEST_STORY_FILE = (
    Path(__file__).resolve().parent / "agent-context" / "test-fixtures" / "synthetic_story.json"
)


@dataclass(frozen=True)
class WorkflowInput:
    repo: str
    change_id: str
    intake_mode: str
    intake_source: str


def _normalize_repo(repo: str | None) -> str:
    return os.path.abspath(repo or os.getcwd())


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file_obj:
        data = json.load(file_obj)

    if not isinstance(data, dict):
        raise ValueError(f"Synthetic story fixture must be a JSON object: {path}")
    return data


def _validate_acceptance_criteria(acceptance_criteria: Any, path: Path) -> None:
    """
    Validate acceptance_criteria field in synthetic story fixture.

    Acceptance criteria must be either:
    - A non-empty list of non-empty strings
    - A non-empty dict with non-empty string keys and non-empty string values

    Args:
        acceptance_criteria: The value to validate from fixture["acceptance_criteria"]
        path: Path to the fixture file (for error messages)

    Raises:
        ValueError: If acceptance_criteria fails any validation rule
    """
    if isinstance(acceptance_criteria, list):
        if not acceptance_criteria or not all(isinstance(item, str) and item.strip() for item in acceptance_criteria):
            raise ValueError(
                f"Synthetic story fixture acceptance_criteria must be a non-empty list of strings: {path}"
            )
        return

    if isinstance(acceptance_criteria, dict):
        if not acceptance_criteria:
            raise ValueError(f"Synthetic story fixture acceptance_criteria must not be empty: {path}")
        invalid_items = [
            key for key, value in acceptance_criteria.items()
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
        ]
        if invalid_items:
            raise ValueError(
                "Synthetic story fixture acceptance_criteria map must use non-empty string keys and values: "
                f"{path}"
            )
        return

    raise ValueError(
        "Synthetic story fixture acceptance_criteria must be either a list of strings or a map of AC ids to strings: "
        f"{path}"
    )


def load_story_fixture(path: str) -> dict[str, Any]:
    """
    Load and validate a synthetic story fixture from a JSON file.

    This function performs comprehensive validation to ensure the fixture is well-formed
    and can be used by downstream stages. It enforces the synthetic story contract:
    1. File must exist
    2. File must be valid JSON (dict)
    3. Must contain required fields: title, description, acceptance_criteria
    4. All required fields must be non-empty
    5. acceptance_criteria must be either:
       - A non-empty list of non-empty strings, or
       - A non-empty dict with non-empty string keys and values
    6. Rejects: empty lists/dicts, None values, whitespace-only strings, non-string types

    Args:
        path: Path to the JSON fixture file (may use ~ for home directory)

    Returns:
        The parsed fixture as a dict, validated to pass all checks above

    Raises:
        FileNotFoundError: If the fixture file does not exist
        ValueError: If JSON is invalid, not a dict, missing required fields, or
                    acceptance_criteria fails validation
    """
    fixture_path = Path(path).expanduser().resolve()
    if not fixture_path.is_file():
        raise FileNotFoundError(f"Synthetic story fixture not found: {fixture_path}")

    fixture = _load_json(fixture_path)
    required_fields = ("title", "description", "acceptance_criteria")
    missing_fields = [field for field in required_fields if not fixture.get(field)]
    if missing_fields:
        raise ValueError(
            f"Synthetic story fixture is missing required field(s) {', '.join(missing_fields)}: {fixture_path}"
        )

    _validate_acceptance_criteria(fixture["acceptance_criteria"], fixture_path)
    return fixture


def infer_change_id_from_ado_url(ado_url: str) -> str | None:
    match = re.search(r"/(?:edit|workItems/edit)/(\d+)/?$", ado_url)
    if not match:
        match = re.search(r"(\d+)/?$", ado_url)
    if not match:
        return None
    return f"WI-{match.group(1)}"


def resolve_workflow_input(
    *,
    repo: str | None = None,
    change_id: str | None = None,
    ado_url: str | None = None,
    story_file: str | None = None,
) -> WorkflowInput:
    if ado_url and story_file:
        raise ValueError("Provide either ado_url or story_file, not both.")

    if not ado_url and not story_file:
        story_file = str(DEFAULT_TEST_STORY_FILE)

    resolved_repo = _normalize_repo(repo)

    if story_file:
        fixture_path = str(Path(story_file).expanduser().resolve())
        fixture = load_story_fixture(fixture_path)
        fixture_change_id = fixture.get("change_id")

        if change_id and fixture_change_id and change_id != fixture_change_id:
            raise ValueError(
                "Synthetic story fixture change_id does not match the runner change_id: "
                f"fixture={fixture_change_id}, runner={change_id}"
            )

        resolved_change_id = change_id or fixture_change_id
        if not resolved_change_id:
            raise ValueError(
                "Synthetic story fixture must include change_id, or the runner must be given one explicitly."
            )

        return WorkflowInput(
            repo=resolved_repo,
            change_id=resolved_change_id,
            intake_mode="synthetic",
            intake_source=fixture_path,
        )

    inferred_change_id = infer_change_id_from_ado_url(ado_url or "")
    resolved_change_id = change_id or inferred_change_id
    if not resolved_change_id:
        raise ValueError("Could not infer change_id from the ADO URL. Please provide change_id explicitly.")

    return WorkflowInput(
        repo=resolved_repo,
        change_id=resolved_change_id,
        intake_mode="ado",
        intake_source=ado_url or "",
    )

