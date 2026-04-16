"""Tests for the auto-materialization hook in materialize.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_runner.materialize import _should_auto_materialize, maybe_materialize


def _make_workspace(tmp_path: Path, *, with_sources: bool, agents_empty: bool | None) -> Path:
    """Create a fake workspace directory structure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if with_sources:
        (repo / "agent-sources").mkdir()
    if agents_empty is not None:
        agents_dir = repo / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        if not agents_empty:
            (agents_dir / "dummy.md").write_text("agent")
    return repo


class TestShouldAutoMaterialize:
    def test_no_sources_dir(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=False, agents_empty=None)
        assert _should_auto_materialize(repo, None) is False

    def test_agents_dir_explicitly_provided(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=None)
        assert _should_auto_materialize(repo, repo / "custom-agents") is False

    def test_agents_already_populated(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=False)
        assert _should_auto_materialize(repo, None) is False

    def test_sources_exist_agents_missing(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=None)
        assert _should_auto_materialize(repo, None) is True

    def test_sources_exist_agents_empty(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=True)
        assert _should_auto_materialize(repo, None) is True


class TestMaybeMaterialize:
    def test_no_sources_is_noop(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=False, agents_empty=None)
        # Should not raise and should not call materialize_for_workflow
        with patch("agent_runner.materialize.materialize_for_workflow") as mock_mat:
            maybe_materialize(repo, agents_dir=None)
            mock_mat.assert_not_called()

    def test_materializes_when_conditions_met(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=None)
        with patch("agent_runner.materialize.materialize_for_workflow", return_value=3) as mock_mat:
            maybe_materialize(repo, agents_dir=None, workflow_id="standard")
            mock_mat.assert_called_once()
            call_kwargs = mock_mat.call_args
            assert call_kwargs.args[0] == "standard"
            assert call_kwargs.kwargs["sources_dir"] == repo / "agent-sources"
            assert call_kwargs.kwargs["target_dir"] == repo / ".claude" / "agents"

    def test_explicit_agents_dir_skips(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=None)
        with patch("agent_runner.materialize.materialize_for_workflow") as mock_mat:
            maybe_materialize(repo, agents_dir=tmp_path / "custom")
            mock_mat.assert_not_called()

    def test_degrades_gracefully_on_error(self, tmp_path: Path) -> None:
        repo = _make_workspace(tmp_path, with_sources=True, agents_empty=None)
        with patch(
            "agent_runner.materialize.materialize_for_workflow",
            side_effect=RuntimeError("registry not available"),
        ):
            # Must not raise
            maybe_materialize(repo, agents_dir=None, workflow_id="standard")
