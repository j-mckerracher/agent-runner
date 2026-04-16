"""Tests for --mode flag and deprecated --dev-mode alias in harness CLI."""
from __future__ import annotations

import sys
from io import StringIO

import pytest

from agent_runner_harness.cli.main import build_parser, _resolve_dev_mode


class TestBuildParser:
    def test_default_is_dev_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["evaluate", "--judge-stub"])
        assert _resolve_dev_mode(args) is True

    def test_mode_dev(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["evaluate", "--mode", "dev", "--judge-stub"])
        assert _resolve_dev_mode(args) is True

    def test_mode_authoritative(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["evaluate", "--mode", "authoritative", "--judge-stub"])
        assert _resolve_dev_mode(args) is False

    def test_deprecated_dev_mode_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        args = parser.parse_args(["evaluate", "--dev-mode", "--judge-stub"])
        result = _resolve_dev_mode(args)
        captured = capsys.readouterr()
        assert result is True
        assert "deprecated" in captured.err

    def test_deprecated_no_dev_mode_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = build_parser()
        args = parser.parse_args(["evaluate", "--no-dev-mode", "--judge-stub"])
        result = _resolve_dev_mode(args)
        captured = capsys.readouterr()
        assert result is False
        assert "deprecated" in captured.err

    def test_mode_takes_priority_over_deprecated(self) -> None:
        parser = build_parser()
        # --mode authoritative wins over --dev-mode alias
        args = parser.parse_args(
            ["evaluate", "--mode", "authoritative", "--dev-mode", "--judge-stub"]
        )
        assert _resolve_dev_mode(args) is False

    def test_invalid_mode_rejected(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["evaluate", "--mode", "invalid"])


class TestAuthoritativeModeRequiresImage:
    """CLI should fail with a helpful error when --mode authoritative is used without --image."""

    def test_authoritative_without_image_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_runner_harness.cli.main import _cmd_evaluate
        import argparse
        from unittest.mock import MagicMock, patch

        # Build a minimal mock Task so load_all doesn't need real files
        mock_task = MagicMock()
        mock_task.id = "task1"

        args = argparse.Namespace(
            task=None,
            corpus=str(tmp_path),
            runs_root=str(tmp_path / "runs"),
            sources="agent-sources",
            k=1,
            parallel=1,
            judge_model="gpt-4",
            judge_stub=True,
            cassette_mode="live",
            mode="authoritative",
            dev_mode_flag=None,
            no_dev_mode_flag=False,
            image=None,
        )

        with patch("agent_runner_harness.corpus.load_all", return_value=[mock_task]):
            rc = _cmd_evaluate(args)

        assert rc == 3
