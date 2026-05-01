"""Story corpus listing from eval/stories/*.json."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import db
from .paths import EVAL_STORIES_ROOT, RUNNER_ROOT

logger = logging.getLogger(__name__)


def _load_story(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("_load_story: ignoring %s because JSON root is not an object", path)
            return None
        logger.debug("_load_story: loaded %s", path.name)
        return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("_load_story: could not load %s: %s", path, exc)
        return None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _acceptance_criteria_count(value: Any) -> int | None:
    if isinstance(value, list):
        valid = [item for item in value if isinstance(item, str) and item.strip()]
        return len(valid) if len(valid) == len(value) and valid else None
    if isinstance(value, dict):
        valid = [
            (key, item)
            for key, item in value.items()
            if isinstance(key, str) and key.strip() and isinstance(item, str) and item.strip()
        ]
        return len(valid) if len(valid) == len(value) and valid else None
    return None


def _validate_story_payload(data: dict[str, Any], path: Path) -> bool:
    change_id = data.get("change_id") or path.stem
    if not _is_nonempty_string(change_id):
        logger.warning("_validate_story_payload: ignoring %s because change_id is not usable", path)
        return False
    if not _is_nonempty_string(data.get("title")):
        logger.warning("_validate_story_payload: ignoring %s because title is missing or empty", path)
        return False
    if not _is_nonempty_string(data.get("description")):
        logger.warning("_validate_story_payload: ignoring %s because description is missing or empty", path)
        return False
    if _acceptance_criteria_count(data.get("acceptance_criteria")) is None:
        logger.warning("_validate_story_payload: ignoring %s because acceptance_criteria is invalid or empty", path)
        return False
    return True


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


def _metadata(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = data.get("metadata")
    raw_metadata = data.get("raw_metadata")
    return (
        metadata if isinstance(metadata, dict) else {},
        raw_metadata if isinstance(raw_metadata, dict) else {},
    )


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _safe_suite_story_path(value: Any) -> str | None:
    """Expose suite YAML provenance without letting odd paths escape the repo."""

    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    base = None if path.is_absolute() else (RUNNER_ROOT if path.parts[:1] == ("eval",) else EVAL_STORIES_ROOT.parent)
    try:
        resolved = path.resolve() if base is None else (base / path).resolve()
    except OSError:
        logger.warning("_safe_suite_story_path: could not resolve suite path %s", value)
        return None
    allowed_roots = (RUNNER_ROOT, EVAL_STORIES_ROOT.parent.resolve())
    if not any(_is_relative_to(resolved, root) for root in allowed_roots):
        logger.warning("_safe_suite_story_path: ignoring suite path outside allowed corpus roots: %s", value)
        return None
    return str(resolved)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_suite_index() -> dict[str, dict[str, Any]]:
    """Best-effort index of generated suite manifests keyed by compatibility story id."""

    suites_root = EVAL_STORIES_ROOT.parent / "suites"
    index: dict[str, dict[str, Any]] = {}
    if not suites_root.is_dir():
        return index
    try:
        from eval.yaml_io import load_yaml_mapping
    except RuntimeError as exc:  # pragma: no cover - only in broken environments
        logger.warning("_load_suite_index: YAML support unavailable: %s", exc)
        return index
    for manifest_path in sorted(suites_root.glob("*/suite_manifest.yaml")):
        try:
            manifest = dict(load_yaml_mapping(manifest_path))
        except Exception as exc:  # defensive: corpus listing must ignore malformed suites
            logger.warning("_load_suite_index: ignoring invalid suite manifest %s: %s", manifest_path, exc)
            continue
        story_ids = manifest.get("compatibility_story_ids")
        if not isinstance(story_ids, list):
            continue
        for story_id in story_ids:
            if isinstance(story_id, str) and story_id.strip():
                index[story_id] = {
                    "suite_tier": manifest.get("suite_tier"),
                    "dataset_id": manifest.get("dataset_id"),
                    "total_checks": manifest.get("total_checks"),
                    "generated_runner": manifest.get("generated_runner"),
                    "generated_model": manifest.get("generated_model"),
                    "suite_manifest_path": str(manifest_path),
                }
    return index


def _story_record(path: Path, data: dict[str, Any], suite_index: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    change_id = data.get("change_id") or path.stem
    metadata, raw_metadata = _metadata(data)
    indexed = (suite_index or {}).get(change_id, {})
    ac_count = _acceptance_criteria_count(data.get("acceptance_criteria")) or 0
    check_count = _first_present(
        data.get("check_count"),
        data.get("total_checks"),
        metadata.get("check_count"),
        metadata.get("total_checks"),
        indexed.get("total_checks"),
        ac_count,
    )
    summary = _job_summary(change_id)
    return {
        "id": change_id,
        "title": data.get("title") or path.stem,
        "workflow": data.get("workflow") or metadata.get("workflow") or "staged-delivery",
        "agent": data.get("agent") or metadata.get("agent") or "code-reviewer",
        "story_file": str(path),
        "suite_tier": _first_present(metadata.get("suite_tier"), raw_metadata.get("tier"), indexed.get("suite_tier")),
        "dataset_id": _first_present(metadata.get("dataset_id"), raw_metadata.get("dataset_id"), indexed.get("dataset_id")),
        "suite_story_path": _safe_suite_story_path(raw_metadata.get("suite_yaml")),
        "acceptance_criteria_count": ac_count,
        "ac_count": ac_count,  # backwards-compatible alias for existing GUI consumers
        "check_count": check_count,
        "total_checks": _first_present(data.get("total_checks"), metadata.get("total_checks"), indexed.get("total_checks"), check_count),
        "generated_runner": _first_present(metadata.get("generated_runner"), raw_metadata.get("generated_runner"), indexed.get("generated_runner")),
        "generated_model": _first_present(metadata.get("generated_model"), raw_metadata.get("generated_model"), indexed.get("generated_model")),
        "suite_manifest_path": indexed.get("suite_manifest_path"),
        "runs": summary["runs"],
        "pass_rate": summary["pass_rate"],
        "last_status": summary["last_status"],
    }


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
    suite_index = _load_suite_index()
    for path in sorted(EVAL_STORIES_ROOT.glob("*.json")):
        data = _load_story(path)
        if data is None or not _validate_story_payload(data, path):
            continue
        out.append(_story_record(path, data, suite_index))
    logger.debug("list_stories: %d story(ies) returned", len(out))
    return out


def get_story(change_id: str) -> dict[str, Any] | None:
    logger.debug("get_story: change_id=%s", change_id)
    if not EVAL_STORIES_ROOT.is_dir():
        logger.warning("get_story: EVAL_STORIES_ROOT does not exist: %s", EVAL_STORIES_ROOT)
        return None
    for path in sorted(EVAL_STORIES_ROOT.glob("*.json")):
        data = _load_story(path)
        if not data or not _validate_story_payload(data, path):
            continue
        if (data.get("change_id") or path.stem) != change_id:
            continue
        logger.debug("get_story: matched %s in file %s", change_id, path.name)
        record = _story_record(path, data, _load_suite_index())
        rows = db.list_jobs(change_id=change_id, run_kind="evaluation", limit=20)
        history = [
            {"id": r["id"], "submitted_at": r["submitted_at"], "status": r["status"]}
            for r in rows
        ]
        return {
            **record,
            "description": data.get("description") or "",
            "acceptance_criteria": _normalize_acs(data.get("acceptance_criteria")),
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
        if not data or not _validate_story_payload(data, path):
            continue
        if (data.get("change_id") or path.stem) == change_id:
            logger.debug("story_path_for: matched %s at %s", change_id, path)
            return path
    logger.debug("story_path_for: change_id=%s not found", change_id)
    return None
