"""Unit tests for ensure_graphify_index in core.repo_prep."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestEnsureGraphifyIndex(unittest.TestCase):
    def setUp(self):
        # Import here so edits to repo_prep are picked up fresh.
        from core.repo_prep import ensure_graphify_index  # noqa: PLC0415

        self.fn = ensure_graphify_index

    # ------------------------------------------------------------------ #
    # Already indexed — graph.json exists                                  #
    # ------------------------------------------------------------------ #
    def test_returns_false_when_already_indexed(self, tmp_path=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "graphify-out").mkdir()
            (repo / "graphify-out" / "graph.json").write_text("{}")
            with patch("subprocess.Popen") as mock_popen:
                result = self.fn(repo)
        self.assertFalse(result)
        mock_popen.assert_not_called()

    # ------------------------------------------------------------------ #
    # Rebuild already in progress — .rebuild.lock exists                  #
    # ------------------------------------------------------------------ #
    def test_returns_false_when_rebuild_lock_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "graphify-out").mkdir()
            (repo / "graphify-out" / ".rebuild.lock").write_text("12345")
            with patch("subprocess.Popen") as mock_popen:
                result = self.fn(repo)
        self.assertFalse(result)
        mock_popen.assert_not_called()

    # ------------------------------------------------------------------ #
    # CLI not installed                                                    #
    # ------------------------------------------------------------------ #
    def test_returns_false_when_cli_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            with patch("shutil.which", return_value=None), \
                 patch("subprocess.Popen") as mock_popen:
                result = self.fn(repo)
        self.assertFalse(result)
        mock_popen.assert_not_called()

    # ------------------------------------------------------------------ #
    # First run — no index, CLI present → launch detached Popen           #
    # ------------------------------------------------------------------ #
    def test_launches_popen_on_first_run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            fake_bin = "/usr/local/bin/graphify"
            mock_proc = MagicMock()
            with patch("shutil.which", return_value=fake_bin), \
                 patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("pathlib.Path.mkdir"):
                result = self.fn(repo)

        self.assertTrue(result)
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        argv = call_args.args[0]
        self.assertEqual(argv[0], fake_bin)
        # resolve() normalises /var → /private/var on macOS
        self.assertEqual(Path(argv[1]).resolve(), repo.resolve())
        self.assertTrue(call_args.kwargs.get("start_new_session"))

    # ------------------------------------------------------------------ #
    # Popen exception → propagates (caller wraps in try/except)           #
    # ------------------------------------------------------------------ #
    def test_propagates_popen_exception(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            with patch("shutil.which", return_value="/usr/bin/graphify"), \
                 patch("subprocess.Popen", side_effect=OSError("no such file")), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("pathlib.Path.mkdir"):
                with self.assertRaises(OSError):
                    self.fn(repo)


if __name__ == "__main__":
    unittest.main()
