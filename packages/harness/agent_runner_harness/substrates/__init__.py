"""Substrate manifest reader.

Reads substrates/substrates.yaml which maps substrate refs to
{repo_url, commit} entries used to prepare working copies.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SubstrateEntry:
    """A single substrate specification."""

    ref: str
    repo_url: str
    commit: str
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return dict representation."""
        return {
            "ref": self.ref,
            "repo_url": self.repo_url,
            "commit": self.commit,
            "description": self.description,
        }


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the substrates manifest YAML.

    Args:
        path: Path to substrates.yaml.

    Returns:
        Parsed manifest dict (version + substrates mapping).

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        ValueError: If the manifest is malformed.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Substrates manifest not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Substrates manifest must be a mapping: {path}")
    if "substrates" not in data:
        raise ValueError(f"Substrates manifest missing 'substrates' key: {path}")
    return data


def resolve(ref: str, manifest: dict[str, Any]) -> SubstrateEntry:
    """Resolve a substrate ref from the manifest.

    Args:
        ref: The substrate ref string (e.g. 'baseline-2026-04-16').
        manifest: Parsed manifest dict from load_manifest().

    Returns:
        SubstrateEntry with repo_url and commit.

    Raises:
        KeyError: If the ref is not found in the manifest.
    """
    substrates = manifest.get("substrates", {})
    if ref not in substrates:
        available = list(substrates.keys())
        raise KeyError(
            f"Substrate ref {ref!r} not found. Available: {available}"
        )
    entry = substrates[ref]
    return SubstrateEntry(
        ref=ref,
        repo_url=entry["repo_url"],
        commit=entry["commit"],
        description=entry.get("description", ""),
    )


def extract_substrate(entry: SubstrateEntry, dest_dir: Path) -> str:
    """Clone the substrate repository and check out the specified commit.

    Supports ``file://`` and ``http(s)://`` repository URLs. The
    ``dest_dir`` must be empty (or non-existent) before the clone.

    Args:
        entry: The substrate to extract.
        dest_dir: Destination directory for the working copy.

    Returns:
        The actual HEAD commit SHA after checkout.

    Raises:
        ValueError: If ``dest_dir`` is not empty.
        RuntimeError: If the git clone or checkout fails.
    """
    dest_dir = Path(dest_dir)

    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise ValueError(f"dest_dir must be empty before clone: {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)

    repo_url = entry.repo_url
    # Strip file:// prefix for local paths so git understands it
    if repo_url.startswith("file://"):
        clone_url = repo_url[len("file://"):]
    else:
        clone_url = repo_url

    clone_result = subprocess.run(
        ["git", "clone", clone_url, str(dest_dir)],
        capture_output=True,
        text=True,
    )
    if clone_result.returncode != 0:
        raise RuntimeError(
            f"git clone failed for {repo_url!r}: {clone_result.stderr.strip()}"
        )

    commit = entry.commit or "HEAD"
    if commit != "HEAD":
        checkout_result = subprocess.run(
            ["git", "-C", str(dest_dir), "checkout", commit],
            capture_output=True,
            text=True,
        )
        if checkout_result.returncode != 0:
            raise RuntimeError(
                f"git checkout {commit!r} failed: {checkout_result.stderr.strip()}"
            )

    rev_result = subprocess.run(
        ["git", "-C", str(dest_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return rev_result.stdout.strip()

