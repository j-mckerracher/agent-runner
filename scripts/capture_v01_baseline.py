#!/usr/bin/env python3
"""Capture a deterministic v0.1 baseline manifest for Agent Workbench.

See docs/refactor/v0.1-baseline.md for what this is, why it exists, and how
future candidate runs should be compared against it.

Usage:
    python3 scripts/capture_v01_baseline.py
    python3 scripts/capture_v01_baseline.py --repo-root /path/to/repo --output out.json
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.file_hash import hash_file  # noqa: E402

UNAVAILABLE = "unavailable"
DIFFICULTY_DIRS = {"easy", "medium", "hard"}

# Curated, not exhaustive: files that shape prompts/config seen by the workflow
# and eval harness. Extend this list as new config surfaces are added.
PROMPT_CONFIG_FILES = [
    "requirements.txt",
    "core/agent_prompts.py",
    ".claude/settings.json",
    ".gemini/settings.json",
    ".serena/project.yml",
]

RUNNER_WORKFLOW_CONFIG_FILES = [
    "eval/runner.py",
    "eval/result_schema.py",
    "eval/seed_benchmarks.py",
    "core/steps.py",
    "core/run_cmds.py",
]


def _run_git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _git_info(repo_root: Path) -> dict[str, Any]:
    commit = _run_git(repo_root, "rev-parse", "HEAD")
    if commit is None:
        return {
            "git_commit": UNAVAILABLE,
            "git_dirty": UNAVAILABLE,
            "note": "git metadata unavailable (not a git repo, no commits yet, or git not on PATH)",
        }
    status = _run_git(repo_root, "status", "--porcelain")
    return {
        "git_commit": commit,
        "git_dirty": bool(status) if status is not None else UNAVAILABLE,
        "note": None,
    }


def _hash_files(repo_root: Path, relative_paths: list[str]) -> dict[str, str]:
    return {rel: hash_file(repo_root / rel) for rel in relative_paths}


def _infer_difficulty(case_dir: Path, story_path: Path) -> str:
    if case_dir.name in DIFFICULTY_DIRS:
        return case_dir.name
    if story_path.is_file():
        try:
            story = json.loads(story_path.read_text(encoding="utf-8"))
            difficulty = story.get("metadata", {}).get("difficulty")
            if isinstance(difficulty, str) and difficulty:
                return difficulty
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return UNAVAILABLE


def discover_benchmarks(repo_root: Path) -> list[dict[str, Any]]:
    """Inventory legacy-structure benchmark cases (story.json + hidden_tests.py)."""
    benchmarks_root = repo_root / "eval" / "benchmarks"
    if not benchmarks_root.is_dir():
        return []
    cases = []
    for case_dir in sorted(p for p in benchmarks_root.iterdir() if p.is_dir()):
        story_path = case_dir / "story.json"
        hidden_tests_path = case_dir / "hidden_tests.py"
        cases.append(
            {
                "benchmark_path": str(case_dir.relative_to(repo_root)),
                "benchmark_id": case_dir.name,
                "difficulty": _infer_difficulty(case_dir, story_path),
                "story_json_present": story_path.is_file(),
                "hidden_tests_present": hidden_tests_path.is_file(),
                "file_hashes": {
                    "story.json": hash_file(story_path),
                    "hidden_tests.py": hash_file(hidden_tests_path),
                },
            }
        )
    return cases


def inventory_dir(repo_root: Path, relative_dir: str, *, max_files: int = 500) -> dict[str, Any]:
    """List files under a (possibly-absent, gitignored) runtime directory.

    `exists=False` is kept distinct from `file_count=0` so an absent directory
    (e.g. a fresh clone with no local run history) is never mistaken for a
    known-empty one.
    """
    dir_path = repo_root / relative_dir
    if not dir_path.is_dir():
        return {"path": relative_dir, "exists": False, "file_count": UNAVAILABLE, "files": []}
    files = sorted(str(p.relative_to(repo_root)) for p in dir_path.rglob("*") if p.is_file())
    return {
        "path": relative_dir,
        "exists": True,
        "file_count": len(files),
        "files": files[:max_files],
        "truncated": len(files) > max_files,
    }


def build_manifest(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    git_info = _git_info(repo_root)
    baseline_id_suffix = git_info["git_commit"] if git_info["git_commit"] != UNAVAILABLE else "no-git"
    return {
        "baseline_id": f"v0.1-{baseline_id_suffix}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "code_version": git_info,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "benchmark_inventory": discover_benchmarks(repo_root),
        "eval_report_inventory": inventory_dir(repo_root, "eval/reports"),
        "trace_log_inventory": inventory_dir(repo_root, "logs"),
        "artifact_inventory": inventory_dir(repo_root, "agent-context"),
        "prompt_config_hashes": _hash_files(repo_root, PROMPT_CONFIG_FILES),
        "runner_workflow_config_hashes": _hash_files(repo_root, RUNNER_WORKFLOW_CONFIG_FILES),
        "notes": [
            "eval/reports, logs, and agent-context are gitignored local runtime "
            "directories; a fresh clone reports exists=false rather than an "
            "empty/zero inventory.",
            "Unavailable git/file metadata is reported as the literal string "
            "'unavailable' (or 'unavailable:missing-file' for hashes), never as "
            "0 or an empty value, so absence is never mistaken for a known zero.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root to inspect (defaults to this repo).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Manifest output path (defaults to <repo-root>/baselines/v0.1/baseline_manifest.json).",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    output_path = args.output or (repo_root / "baselines" / "v0.1" / "baseline_manifest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(repo_root)
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote baseline manifest to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
