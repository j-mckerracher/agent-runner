"""Corpus reader — reads eval/stories/*.json for the Corpus view."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.db import get_conn

_RUNNER_ROOT = Path(__file__).resolve().parent.parent
_STORIES_DIR = _RUNNER_ROOT / "eval" / "stories"


def _load_story_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _pass_rate_for_change_id(change_id: str, last_n: int = 10) -> list[dict[str, Any]]:
    """Return last N job outcomes for a given change_id from jobs table."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT id, status, submitted_at, exit_code
               FROM jobs
               WHERE change_id = ?
               ORDER BY submitted_at DESC
               LIMIT ?""",
            (change_id, last_n),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def list_corpus() -> list[dict[str, Any]]:
    """Return summary list of all story fixtures."""
    items: list[dict[str, Any]] = []
    if not _STORIES_DIR.exists():
        return items

    for story_path in sorted(_STORIES_DIR.glob("*.json")):
        data = _load_story_file(story_path)
        if not data:
            continue
        change_id = data.get("change_id", story_path.stem)
        history = _pass_rate_for_change_id(change_id)
        succeeded = sum(1 for h in history if h["status"] == "succeeded")
        total = len(history)
        items.append({
            "id": change_id,
            "title": data.get("title", change_id),
            "description": data.get("description", ""),
            "ac_count": len(data.get("acceptance_criteria", [])),
            "pass_rate": (succeeded / total) if total > 0 else None,
            "run_count": total,
            "path": str(story_path),
        })
    return items


def get_corpus_item(story_id: str) -> dict[str, Any] | None:
    """Return full story detail with pass-rate history."""
    if not _STORIES_DIR.exists():
        return None

    # Try exact filename match first
    for story_path in _STORIES_DIR.glob("*.json"):
        data = _load_story_file(story_path)
        if not data:
            continue
        change_id = data.get("change_id", story_path.stem)
        if change_id == story_id or story_path.stem == story_id:
            history = _pass_rate_for_change_id(change_id)
            return {
                **data,
                "id": change_id,
                "pass_rate_history": history,
            }
    return None
