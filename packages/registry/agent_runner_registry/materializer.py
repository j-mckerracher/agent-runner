"""Materialize resolved bundles into a .claude/agents/ target directory.

Writes one `.agent.md` file per bundle. Records content hashes so runs
can verify materialization integrity later. If `target_dir` exists it is
cleared first (registry owns its materialization target).
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Iterable

from agent_runner_shared.models import AgentRef, MaterializationManifest
from agent_runner_shared.util import iso_now

from .loader import Bundle


def _write_agent_file(bundle: Bundle, target_dir: Path) -> tuple[Path, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = bundle.claude_code_filename
    dest = target_dir / filename
    text = bundle.prompt_text
    if not text.strip():
        text = f"# {bundle.ref.name}\n\n(empty agent prompt)\n"
    dest.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return dest, digest


def materialize(
    bundles: Iterable[Bundle],
    target_dir: Path,
    *,
    clean: bool = True,
) -> MaterializationManifest:
    target_dir = Path(target_dir)
    if clean and target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    refs: list[AgentRef] = []
    hashes: dict[str, str] = {}
    for bundle in bundles:
        dest, digest = _write_agent_file(bundle, target_dir)
        refs.append(bundle.ref)
        hashes[str(bundle.ref)] = digest

    manifest = MaterializationManifest(
        agents=refs,
        target_dir=str(target_dir),
        content_hashes=hashes,
        materialized_at=iso_now(),
    )
    (target_dir / ".materialization.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest
