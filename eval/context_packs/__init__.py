"""Versioned context-pack loader for single-agent evals."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


_CONTEXT_PACK_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ContextPack:
    id: str
    agent: str
    path: Path | None
    sha256: str
    description: str
    payload: dict[str, Any]
    rendered: str


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _render_value(value: Any, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            lines.append(f"{prefix}- {key}:")
            lines.extend(_render_value(item, indent=indent + 2))
        return lines
    if isinstance(value, list):
        return [f"{prefix}- {item}" for item in value]
    return [f"{prefix}{value}"]


def render_context_pack(payload: Mapping[str, Any]) -> str:
    lines = [f"# Context pack: {payload.get('id', 'unknown')}", ""]
    if payload.get("description"):
        lines.extend([str(payload["description"]), ""])
    for section in (
        "prompt_addendum",
        "schema_summary",
        "quality_checklist",
        "examples",
        "includes",
        "excludes",
        "repo_context_rules",
        "optimization_notes",
        "budget",
    ):
        value = payload.get(section)
        if value in (None, "", [], {}):
            continue
        title = section.replace("_", " ").title()
        lines.extend([f"## {title}"])
        lines.extend(_render_value(value))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def available_context_packs(agent: str) -> list[str]:
    directory = _CONTEXT_PACK_ROOT / agent
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.yaml"))


def load_context_pack(agent: str, context_pack: str | None = "baseline") -> ContextPack:
    if context_pack in (None, "", "none"):
        return ContextPack(
            id=f"{agent}.context.none",
            agent=agent,
            path=None,
            sha256=_sha256_text(""),
            description="No additional context pack.",
            payload={},
            rendered="",
        )
    path = _CONTEXT_PACK_ROOT / agent / f"{context_pack}.yaml"
    if not path.is_file():
        choices = ", ".join(available_context_packs(agent)) or "none"
        raise FileNotFoundError(f"unknown context pack {context_pack!r} for {agent}; available: {choices}")
    raw_text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw_text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"context pack must be a YAML mapping: {path}")
    payload.setdefault("id", f"{agent}.context.{context_pack}")
    payload.setdefault("agent", agent)
    rendered = render_context_pack(payload)
    return ContextPack(
        id=str(payload.get("id")),
        agent=str(payload.get("agent") or agent),
        path=path,
        sha256=_sha256_text(raw_text),
        description=str(payload.get("description") or ""),
        payload=dict(payload),
        rendered=rendered,
    )
