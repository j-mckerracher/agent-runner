"""Bundle loader: discovers and validates agent bundles under a sources dir."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent_runner_shared.models import AgentRef


@dataclass(frozen=True)
class Bundle:
    ref: AgentRef
    root: Path
    manifest: dict
    prompt_text: str

    @property
    def claude_code_filename(self) -> str:
        return self.manifest.get("claude_code_agent_file") or f"{self.ref.name}.agent.md"


def _load_manifest(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a mapping: {path}")
    return data


def load_bundles(sources_dir: Path) -> dict[AgentRef, Bundle]:
    """Walk `sources_dir` and load all discovered bundles.

    Expected layout:
        sources_dir/<name>/<version>/manifest.yaml
                                     prompt.md
                                     [tools.json]
                                     [config.yaml]
    """
    sources_dir = Path(sources_dir)
    if not sources_dir.is_dir():
        return {}
    bundles: dict[AgentRef, Bundle] = {}
    for name_dir in sorted(p for p in sources_dir.iterdir() if p.is_dir()):
        for ver_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
            manifest_path = next(
                (p for p in (ver_dir / "manifest.yaml", ver_dir / "manifest.yml", ver_dir / "manifest.json")
                 if p.exists()),
                None,
            )
            if manifest_path is None:
                continue
            manifest = _load_manifest(manifest_path)
            name = manifest.get("name") or name_dir.name
            version = manifest.get("version") or ver_dir.name
            prompt_path = ver_dir / "prompt.md"
            prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
            ref = AgentRef(name=name, version=str(version))
            bundles[ref] = Bundle(
                ref=ref,
                root=ver_dir,
                manifest=manifest,
                prompt_text=prompt_text,
            )
    return bundles
