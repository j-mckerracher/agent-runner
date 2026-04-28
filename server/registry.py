"""Registry reader — reads agent-sources/ for the Agents view."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_RUNNER_ROOT = Path(__file__).resolve().parent.parent
_AGENT_SOURCES = _RUNNER_ROOT / "agent-sources"


def _hash_dir(path: Path) -> str:
    """Compute a short SHA256 hash over the text content of all files in a directory."""
    h = hashlib.sha256()
    try:
        for f in sorted(path.rglob("*")):
            if f.is_file():
                try:
                    h.update(f.name.encode())
                    h.update(f.read_bytes())
                except OSError:
                    pass
    except OSError:
        pass
    return h.hexdigest()[:12]


def _parse_tags_from_spec(text: str) -> list[str]:
    """Extract tags from agent spec markdown (looks for a Tags: line or ## Tags section)."""
    tags: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^Tags?:\s*(.+)$", line, re.IGNORECASE)
        if m:
            tags = [t.strip() for t in re.split(r"[,;]", m.group(1)) if t.strip()]
            break
    return tags


def list_agents() -> list[dict[str, Any]]:
    """Scan agent-sources/*/v*/ and return agent registry entries."""
    agents: list[dict[str, Any]] = []
    if not _AGENT_SOURCES.exists():
        return agents

    for agent_dir in sorted(_AGENT_SOURCES.iterdir()):
        if not agent_dir.is_dir():
            continue
        name = agent_dir.name
        # Look for version directories (v1, v2, ...)
        version_dirs = sorted(
            [d for d in agent_dir.iterdir() if d.is_dir() and re.match(r"^v\d+$", d.name)],
            key=lambda d: int(d.name[1:]),
        )
        if not version_dirs:
            # Treat the agent dir itself as v1
            version_dirs = [agent_dir]

        for version_dir in version_dirs:
            version = version_dir.name if version_dir != agent_dir else "v1"
            bundle_hash = _hash_dir(version_dir)

            # Try to read spec file for tags
            tags: list[str] = []
            for spec_name in ("AGENT.md", "agent.md", "spec.md", "SPEC.md"):
                spec_file = version_dir / spec_name
                if spec_file.exists():
                    try:
                        tags = _parse_tags_from_spec(spec_file.read_text(encoding="utf-8"))
                    except OSError:
                        pass
                    break

            agents.append({
                "name": name,
                "version": version,
                "path": str(version_dir),
                "bundle_hash": bundle_hash,
                "tags": tags,
            })

    return agents
