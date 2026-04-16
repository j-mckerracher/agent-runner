"""Tests for extract_substrate using a local git repository."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner_harness.substrates import SubstrateEntry, extract_substrate


def _init_git_repo(path: Path) -> str:
    """Create a minimal git repo with one commit and return its SHA."""
    path.mkdir(parents=True, exist_ok=True)
    cmds = [
        ["git", "init", str(path)],
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        ["git", "-C", str(path), "config", "user.name", "Test"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=True, capture_output=True)

    (path / "README.md").write_text("# test repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestExtractSubstrate:
    def test_file_url_clone_and_head_sha(self, tmp_path: Path) -> None:
        """Clone a local repo via file:// URL and verify HEAD SHA matches."""
        src = tmp_path / "src_repo"
        expected_sha = _init_git_repo(src)

        dest = tmp_path / "dest"
        entry = SubstrateEntry(
            ref="test",
            repo_url=f"file://{src}",
            commit="HEAD",
        )
        actual_sha = extract_substrate(entry, dest)

        assert actual_sha == expected_sha
        assert (dest / "README.md").exists()

    def test_specific_commit_checkout(self, tmp_path: Path) -> None:
        """Cloning at a specific commit SHA returns that exact commit."""
        src = tmp_path / "src_repo"
        first_sha = _init_git_repo(src)

        # Add a second commit
        (src / "extra.txt").write_text("extra\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(src), "commit", "-m", "second"],
            check=True,
            capture_output=True,
        )

        dest = tmp_path / "dest"
        entry = SubstrateEntry(
            ref="test",
            repo_url=f"file://{src}",
            commit=first_sha,
        )
        actual_sha = extract_substrate(entry, dest)
        assert actual_sha == first_sha

    def test_raises_on_nonempty_dest(self, tmp_path: Path) -> None:
        """extract_substrate should raise ValueError if dest is not empty."""
        src = tmp_path / "src_repo"
        _init_git_repo(src)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "existing.txt").write_text("data", encoding="utf-8")

        entry = SubstrateEntry(
            ref="test",
            repo_url=f"file://{src}",
            commit="HEAD",
        )
        with pytest.raises(ValueError, match="empty"):
            extract_substrate(entry, dest)

    def test_raises_on_bad_url(self, tmp_path: Path) -> None:
        """extract_substrate should raise RuntimeError when git clone fails."""
        dest = tmp_path / "dest"
        entry = SubstrateEntry(
            ref="test",
            repo_url="file:///nonexistent/path/to/nowhere",
            commit="HEAD",
        )
        with pytest.raises(RuntimeError, match="git clone failed"):
            extract_substrate(entry, dest)
