"""End-to-end test for the calibration workflow.

This test invokes the `calibrate` subcommand via subprocess using the
`simple-readme-update` seed task (the minimal corpus task). It verifies that:
  1. The command exits successfully (exit code 0).
  2. A `baselines/<task_id>.json` file is produced in a temporary directory.
  3. The band JSON has the expected structure (task_id, low, high, mean, sample_size).

Implementation notes:
  - We use `--judge-stub` so no real LLM calls are made; rubric criteria
    are stubbed to always pass.
  - We use `--runs 3` (via `--k 3`) to keep the test fast while still
    exercising multi-run band computation.
  - We redirect `--baselines-dir` and `--runs-root` to `tmp_path` so the
    test does not write to the real `baselines/` or `runs/` directories.
  - We invoke the harness via `python -m agent_runner_harness.cli.main`
    with `PYTHONPATH` set, since the package may not be installed as a
    console script in all environments.
  - Corpus and agent-sources paths point to the real repo-root directories;
    these are read-only during the test.
  - The runner is invoked in `--dry-run` mode internally (dev mode default),
    so no Claude Code subprocess or real workflow execution occurs. Artifacts
    produced in dry-run mode may be minimal/empty, causing deterministic
    criteria to fail — this is expected and tested: we verify the band
    structure regardless of pass/fail values.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root: two levels up from this file (packages/harness/tests/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "task-corpus"
SOURCES_DIR = REPO_ROOT / "agent-sources"
TASK_ID = "simple-readme-update"


def _pythonpath() -> str:
    """Return a PYTHONPATH string that includes all four packages."""
    packages = [
        REPO_ROOT / "packages" / "shared",
        REPO_ROOT / "packages" / "runner",
        REPO_ROOT / "packages" / "registry",
        REPO_ROOT / "packages" / "harness",
    ]
    existing = os.environ.get("PYTHONPATH", "")
    parts = [str(p) for p in packages]
    if existing:
        parts.append(existing)
    return ":".join(parts)


class TestCalibrateSubcommand:
    def test_calibrate_produces_baseline_band(self, tmp_path: Path) -> None:
        """Running `calibrate` with --judge-stub writes a valid band JSON."""
        baselines_dir = tmp_path / "baselines"
        runs_dir = tmp_path / "runs"

        cmd = [
            sys.executable,
            "-m",
            "agent_runner_harness.cli.main",
            "calibrate",
            "--task", TASK_ID,
            "--corpus", str(CORPUS_DIR),
            "--sources", str(SOURCES_DIR),
            "--baselines-dir", str(baselines_dir),
            "--runs-root", str(runs_dir),
            "--k", "3",
            "--judge-stub",
            "--dev-mode",
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(REPO_ROOT),
        )

        assert result.returncode == 0, (
            f"calibrate exited with {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Verify the band file was created
        band_file = baselines_dir / f"{TASK_ID}.json"
        assert band_file.exists(), (
            f"Expected band file at {band_file} but it was not created.\n"
            f"stdout: {result.stdout}"
        )

        # Load and validate the band structure
        band = json.loads(band_file.read_text(encoding="utf-8"))

        assert band["task_id"] == TASK_ID
        assert band["task_version"] == 1
        assert isinstance(band["low"], float)
        assert isinstance(band["high"], float)
        assert isinstance(band["mean"], float)
        assert isinstance(band["sample_size"], int)
        assert band["sample_size"] == 3, (
            f"Expected sample_size=3 (--k 3), got {band['sample_size']}"
        )

        # Band invariants: 0 <= low <= mean <= high <= 1
        assert 0.0 <= band["low"] <= band["mean"] <= band["high"] <= 1.0, (
            f"Band invariant violated: {band}"
        )

        # All required fields present
        required_fields = {
            "task_id", "task_version", "low", "high", "mean",
            "sample_size", "established_at", "judge_model", "reason",
        }
        missing = required_fields - set(band.keys())
        assert not missing, f"Band JSON missing fields: {missing}"

    def test_calibrate_band_width_with_multiple_runs(self, tmp_path: Path) -> None:
        """With k>=2 runs, band width should be mean ± 0.15 (clamped to [0,1])."""
        baselines_dir = tmp_path / "baselines"
        runs_dir = tmp_path / "runs"

        cmd = [
            sys.executable,
            "-m",
            "agent_runner_harness.cli.main",
            "calibrate",
            "--task", TASK_ID,
            "--corpus", str(CORPUS_DIR),
            "--sources", str(SOURCES_DIR),
            "--baselines-dir", str(baselines_dir),
            "--runs-root", str(runs_dir),
            "--k", "3",
            "--judge-stub",
            "--dev-mode",
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0

        band = json.loads((baselines_dir / f"{TASK_ID}.json").read_text())

        # With k>=2, band = mean ± 0.15 clamped to [0, 1]
        mean = band["mean"]
        expected_low = max(0.0, mean - 0.15)
        expected_high = min(1.0, mean + 0.15)
        assert abs(band["low"] - expected_low) < 1e-9, (
            f"low={band['low']} != expected {expected_low} for mean={mean}"
        )
        assert abs(band["high"] - expected_high) < 1e-9, (
            f"high={band['high']} != expected {expected_high} for mean={mean}"
        )

    def test_calibrate_runs_dir_populated(self, tmp_path: Path) -> None:
        """calibrate creates run subdirectories with grading.json files."""
        baselines_dir = tmp_path / "baselines"
        runs_dir = tmp_path / "runs"

        cmd = [
            sys.executable,
            "-m",
            "agent_runner_harness.cli.main",
            "calibrate",
            "--task", TASK_ID,
            "--corpus", str(CORPUS_DIR),
            "--sources", str(SOURCES_DIR),
            "--baselines-dir", str(baselines_dir),
            "--runs-root", str(runs_dir),
            "--k", "3",
            "--judge-stub",
            "--dev-mode",
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath()

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0

        # Verify 3 grading.json files (one per run) under runs/
        grading_files = list(runs_dir.glob("*/*/grading.json"))
        assert len(grading_files) == 3, (
            f"Expected 3 grading.json files, found {len(grading_files)}.\n"
            f"runs_dir contents: {list(runs_dir.rglob('*'))}"
        )

        # Each grading record must have the right task_id
        for gf in grading_files:
            record = json.loads(gf.read_text())
            assert record["task_id"] == TASK_ID
            assert "overall_pass" in record
