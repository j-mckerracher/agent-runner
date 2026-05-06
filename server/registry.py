"""Read agent registry from agent-definition-source/<name>/v*/."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from .paths import AGENT_SOURCES_ROOT

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^v\d+(?:\.\d+)*$")


def _version_key(version_dir: Path) -> tuple[int, ...]:
    return tuple(int(x) for x in version_dir.name[1:].split("."))


def _bundle_hash(version_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(version_dir.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(version_dir)).encode("utf-8"))
            try:
                h.update(p.read_bytes())
            except OSError:
                pass
    return h.hexdigest()[:7]


def _extract_tags(prompt_text: str) -> list[str]:
    """Best-effort extraction of `tags:` from YAML frontmatter, if present."""
    tags: list[str] = []
    if not prompt_text.startswith("---"):
        return tags
    end = prompt_text.find("\n---", 3)
    if end == -1:
        return tags
    frontmatter = prompt_text[3:end]
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("tags:"):
            after = stripped[len("tags:"):].strip()
            if after.startswith("[") and after.endswith("]"):
                inner = after[1:-1]
                tags = [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
            break
    return [t for t in tags if t]


def _version_dirs(agent_dir: Path) -> list[Path]:
    return [d for d in agent_dir.iterdir() if d.is_dir() and _VERSION_RE.match(d.name)]


def _read_prompt_text(prompt_md: Path) -> str:
    try:
        return prompt_md.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("_read_prompt_text: could not read %s: %s", prompt_md, exc)
        return ""


def _agent_summary(agent_dir: Path) -> dict[str, Any] | None:
    versions = _version_dirs(agent_dir)
    if not versions:
        logger.debug("_agent_summary: %s has no versioned directories; skipping", agent_dir.name)
        return None
    latest = max(versions, key=_version_key)
    prompt_md = latest / "prompt.md"
    prompt_text = _read_prompt_text(prompt_md) if prompt_md.is_file() else ""
    tags = _extract_tags(prompt_text) if prompt_text else []
    bundle_hash = _bundle_hash(latest)
    return {
        "name": agent_dir.name,
        "version": latest.name,
        "bundle_hash": bundle_hash,
        "tags": tags,
        "judge_model": None,
        "status": "active",
        "all_versions": [v.name for v in sorted(versions, key=_version_key)],
        "prompt_file": str(prompt_md) if prompt_md.is_file() else None,
        "prompt_text": prompt_text,
    }


def list_agents() -> list[dict[str, Any]]:
    logger.debug("list_agents: scanning AGENT_SOURCES_ROOT=%s", AGENT_SOURCES_ROOT)
    out: list[dict[str, Any]] = []
    if not AGENT_SOURCES_ROOT.is_dir():
        logger.warning("list_agents: AGENT_SOURCES_ROOT does not exist: %s", AGENT_SOURCES_ROOT)
        return out
    for agent_dir in sorted(AGENT_SOURCES_ROOT.iterdir()):
        if not agent_dir.is_dir():
            continue
        summary = _agent_summary(agent_dir)
        if summary is None:
            continue
        logger.debug(
            "list_agents: %s version=%s hash=%s tags=%s",
            summary["name"], summary["version"], summary["bundle_hash"], summary["tags"],
        )
        out.append({k: v for k, v in summary.items() if k not in ("prompt_file", "prompt_text")})
    logger.debug("list_agents: total %d agent(s) found", len(out))
    return out


def get_agent(name: str) -> dict[str, Any] | None:
    logger.debug("get_agent: name=%s", name)
    agent_dir = AGENT_SOURCES_ROOT / name
    if not agent_dir.is_dir():
        logger.debug("get_agent: %s not found", name)
        return None
    summary = _agent_summary(agent_dir)
    if summary is None:
        logger.debug("get_agent: %s has no versioned prompt directories", name)
        return None
    return summary
