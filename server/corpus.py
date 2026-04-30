"""Story corpus listing from eval/stories/*.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import db
from .paths import EVAL_STORIES_ROOT

logger = logging.getLogger(__name__)


def _load_story(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("_load_story: loaded %s", path.name)
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("_load_story: could not load %s: %s", path, exc)
        return None


def _normalize_acs(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [{"id": f"AC-{i+1}", "text": str(v)} for i, v in enumerate(value)]
    if isinstance(value, dict):
        out = []
        for i, (k, v) in enumerate(value.items()):
            out.append({"id": str(k) or f"AC-{i+1}", "text": str(v)})
        return out
    return [{"id": "AC-1", "text": str(value)}]


def _job_summary(change_id: str) -> dict[str, Any]:
    logger.debug("_job_summary: change_id=%s", change_id)
    rows = db.list_jobs(change_id=change_id, run_kind="evaluation", limit=200)
    total = len(rows)
    succ = sum(1 for r in rows if r["status"] == "succeeded")
    rate = round(100.0 * succ / total) if total else None
    last = rows[0]["status"] if rows else None
    logger.debug("_job_summary: change_id=%s total=%d succ=%d pass_rate=%s", change_id, total, succ, rate)
    return {"runs": total, "pass_rate": rate, "last_status": last}


def list_stories() -> list[dict[str, Any]]:
    logger.debug("list_stories: EVAL_STORIES_ROOT=%s", EVAL_STORIES_ROOT)
    out: list[dict[str, Any]] = []
    if not EVAL_STORIES_ROOT.is_dir():
        logger.warning("list_stories: EVAL_STORIES_ROOT does not exist: %s", EVAL_STORIES_ROOT)
        return out
    for path in sorted(EVAL_STORIES_ROOT.glob("*.json")):
        data = _load_story(path)
        if data is None:
            continue
        change_id = data.get("change_id") or path.stem
        summary = _job_summary(change_id)
        out.append({
            "id": change_id,
            "title": data.get("title") or path.stem,
            "workflow": data.get("workflow") or "staged-delivery",
            "agent": data.get("agent") or "code-reviewer",
            "story_file": str(path),
            "runs": summary["runs"],
            "pass_rate": summary["pass_rate"],
            "last_status": summary["last_status"],
        })
    logger.debug("list_stories: %d story(ies) returned", len(out))
    return out


def get_story(change_id: str) -> dict[str, Any] | None:
    logger.debug("get_story: change_id=%s", change_id)
    if not EVAL_STORIES_ROOT.is_dir():
        logger.warning("get_story: EVAL_STORIES_ROOT does not exist: %s", EVAL_STORIES_ROOT)
        return None
    for path in sorted(EVAL_STORIES_ROOT.glob("*.json")):
        data = _load_story(path)
        if not data:
            continue
        if (data.get("change_id") or path.stem) != change_id:
            continue
        logger.debug("get_story: matched %s in file %s", change_id, path.name)
        summary = _job_summary(change_id)
        rows = db.list_jobs(change_id=change_id, run_kind="evaluation", limit=20)
        history = [
            {"id": r["id"], "submitted_at": r["submitted_at"], "status": r["status"]}
            for r in rows
        ]
        return {
            "id": change_id,
            "title": data.get("title") or path.stem,
            "story_file": str(path),
            "description": data.get("description") or "",
            "workflow": data.get("workflow") or "staged-delivery",
            "agent": data.get("agent") or "code-reviewer",
            "acceptance_criteria": _normalize_acs(data.get("acceptance_criteria")),
            "runs": summary["runs"],
            "pass_rate": summary["pass_rate"],
            "last_status": summary["last_status"],
            "history": history,
        }
    logger.debug("get_story: change_id=%s not found in corpus", change_id)
    return None


def story_path_for(change_id: str) -> Path | None:
    logger.debug("story_path_for: change_id=%s", change_id)
    if not EVAL_STORIES_ROOT.is_dir():
        logger.warning("story_path_for: EVAL_STORIES_ROOT does not exist: %s", EVAL_STORIES_ROOT)
        return None
    for path in sorted(EVAL_STORIES_ROOT.glob("*.json")):
        data = _load_story(path)
        if not data:
            continue
        if (data.get("change_id") or path.stem) == change_id:
            logger.debug("story_path_for: matched %s at %s", change_id, path)
            return path
    logger.debug("story_path_for: change_id=%s not found", change_id)
    return None
