"""Tests for the agent registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner_registry import load_bundles, materialize, resolve


@pytest.fixture()
def sources_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "agent-sources"


def test_load_bundles_finds_seeded_agents(sources_dir: Path) -> None:
    bundles = load_bundles(sources_dir)
    names = {b.ref.name for b in bundles.values()}
    assert "intake" in names
    assert "qa" in names
    assert "task-generator" in names


def test_resolve_and_materialize(tmp_path: Path, sources_dir: Path) -> None:
    bundles = load_bundles(sources_dir)
    resolved = resolve(["intake@v1", "qa@v1"], bundles)
    manifest = materialize(resolved, tmp_path / "agents")
    assert len(manifest.agents) == 2
    assert (tmp_path / "agents" / "01-intake.agent.md").is_file()
    assert (tmp_path / "agents" / ".materialization.json").is_file()


def test_resolve_missing_raises(sources_dir: Path) -> None:
    bundles = load_bundles(sources_dir)
    with pytest.raises(LookupError):
        resolve(["no-such-agent@v1"], bundles)
