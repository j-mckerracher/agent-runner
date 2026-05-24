from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

_MANUAL_REQUIRED_FIELDS = ("title", "description", "acceptance_criteria")
_SAFE_CHANGE_ID_CHARS = re.compile(r"[^A-Za-z0-9_.:-]+")
_BULLET_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
_AC_LINE = re.compile(r"^\s*AC\d+\s*[:\-]\s*(.+?)\s*$", re.IGNORECASE)


def _trim_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_manual_acceptance_criteria(value: Any) -> Any:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, dict):
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and isinstance(item, str) and item.strip()
        }
    return _trim_string(value)


def parse_acceptance_criteria_text(text: str) -> dict[str, str]:
    lines = text.splitlines()
    items: list[list[str]] = []
    current: list[str] | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        ac_match = _AC_LINE.match(raw_line)
        bullet_match = _BULLET_LINE.match(raw_line)
        if ac_match or bullet_match:
            if current:
                items.append(current)
            current = [ac_match.group(1).strip() if ac_match else bullet_match.group(1).strip()]
            continue
        if current is not None:
            current.append(stripped)

    if current:
        items.append(current)

    if items:
        return {
            f"AC{index}": " ".join(part for part in parts if part).strip()
            for index, parts in enumerate(items, start=1)
        }

    fallback = text.strip()
    return {"AC1": fallback} if fallback else {}


def normalize_acceptance_criteria(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        cleaned = [
            str(item).strip()
            for key, item in value.items()
            if isinstance(key, str) and key.strip() and isinstance(item, str) and item.strip()
        ]
        return {f"AC{index}": item for index, item in enumerate(cleaned, start=1)}
    if isinstance(value, list):
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return {f"AC{index}": item for index, item in enumerate(cleaned, start=1)}
    if isinstance(value, str):
        return parse_acceptance_criteria_text(value)
    return {}


def count_acceptance_criteria(value: Any) -> int | None:
    normalized = normalize_acceptance_criteria(value)
    return len(normalized) if normalized else None


def sanitize_change_id(value: str) -> str:
    sanitized = _SAFE_CHANGE_ID_CHARS.sub("-", value.strip()).strip("-")
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    if not sanitized:
        raise ValueError("change_id must contain at least one safe character")
    return sanitized[:96]


def _slugify_title(value: str | None, *, fallback: str, max_words: int = 6, limit: int = 48) -> str:
    parts = re.findall(r"[a-z0-9]+", (value or "").lower())[:max_words]
    slug = "-".join(parts).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].strip("-")
    return slug or fallback


def infer_manual_change_id(manual_story: dict[str, Any], explicit_change_id: str | None = None) -> str:
    if explicit_change_id and explicit_change_id.strip():
        return sanitize_change_id(explicit_change_id)

    work_item_id = _trim_string(manual_story.get("work_item_id"))
    if work_item_id:
        return sanitize_change_id(f"WI-{work_item_id}")

    title_slug = _slugify_title(_trim_string(manual_story.get("title")), fallback="story")
    return sanitize_change_id(f"manual-{title_slug}-{secrets.token_hex(3)}")


def validate_manual_story(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("manual_story must be a JSON object")

    story = {
        "work_item_id": _trim_string(payload.get("work_item_id")),
        "work_item_url": _trim_string(payload.get("work_item_url")),
        "title": _trim_string(payload.get("title")),
        "description": _trim_string(payload.get("description")),
        "acceptance_criteria": _clean_manual_acceptance_criteria(payload.get("acceptance_criteria")),
        "extra_context": _trim_string(payload.get("extra_context")),
    }

    missing_fields = [field for field in _MANUAL_REQUIRED_FIELDS if not story.get(field)]
    if missing_fields:
        raise ValueError(
            f"manual_story is missing required field(s) {', '.join(missing_fields)}"
        )

    normalized_ac = normalize_acceptance_criteria(story["acceptance_criteria"])
    if not normalized_ac:
        raise ValueError("manual_story acceptance_criteria must contain at least one non-empty item")

    return {key: value for key, value in story.items() if value is not None}


def load_manual_story(path: str) -> dict[str, Any]:
    story_path = Path(path).expanduser().resolve()
    if not story_path.is_file():
        raise FileNotFoundError(f"Manual story file not found: {story_path}")
    with story_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return validate_manual_story(payload)
