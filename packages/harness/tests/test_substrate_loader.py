"""Tests for the substrate manifest loader."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_runner_harness.substrates import load_manifest, resolve, SubstrateEntry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SUBSTRATES_FILE = REPO_ROOT / "substrates" / "substrates.yaml"


class TestLoadManifest:
    def test_loads_seed_manifest(self) -> None:
        """load_manifest loads the seed substrates.yaml."""
        manifest = load_manifest(SUBSTRATES_FILE)
        assert isinstance(manifest, dict)
        assert "substrates" in manifest
        assert manifest.get("version") == 1

    def test_seed_contains_baseline_ref(self) -> None:
        """Seed manifest contains the baseline-2026-04-16 ref."""
        manifest = load_manifest(SUBSTRATES_FILE)
        assert "baseline-2026-04-16" in manifest["substrates"]

    def test_missing_file_raises(self) -> None:
        """load_manifest raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_manifest(Path("/nonexistent/substrates.yaml"))

    def test_malformed_file_raises(self, tmp_path: Path) -> None:
        """load_manifest raises ValueError for missing 'substrates' key."""
        bad_file = tmp_path / "substrates.yaml"
        bad_file.write_text(yaml.dump({"version": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="substrates"):
            load_manifest(bad_file)


class TestResolve:
    def test_resolves_seed_ref(self) -> None:
        """resolve returns SubstrateEntry for the seed ref."""
        manifest = load_manifest(SUBSTRATES_FILE)
        entry = resolve("baseline-2026-04-16", manifest)
        assert isinstance(entry, SubstrateEntry)
        assert entry.ref == "baseline-2026-04-16"
        assert entry.repo_url
        assert entry.commit

    def test_resolve_returns_correct_url(self) -> None:
        """Resolved entry has the expected repo_url."""
        manifest = load_manifest(SUBSTRATES_FILE)
        entry = resolve("baseline-2026-04-16", manifest)
        assert "agent-runner" in entry.repo_url

    def test_resolve_missing_raises(self) -> None:
        """resolve raises KeyError for unknown ref."""
        manifest = load_manifest(SUBSTRATES_FILE)
        with pytest.raises(KeyError):
            resolve("nonexistent-substrate-ref", manifest)

    def test_resolve_custom_manifest(self) -> None:
        """resolve works with an in-memory manifest."""
        manifest = {
            "version": 1,
            "substrates": {
                "test-ref": {
                    "repo_url": "https://example.com/repo.git",
                    "commit": "abc123",
                    "description": "Test substrate",
                }
            },
        }
        entry = resolve("test-ref", manifest)
        assert entry.commit == "abc123"
        assert entry.repo_url == "https://example.com/repo.git"
        assert entry.description == "Test substrate"
