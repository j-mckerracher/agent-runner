"""Tests for rebaseline check command (pG-rebaseline)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner_harness.baseline.manager import compute_band, save_band
from agent_runner_harness.cli.main import build_parser, main
from agent_runner_shared.models import BaselineBand


def _write_band(baselines_dir: Path, task_id: str, judge_model: str, reason: str) -> Path:
    band = BaselineBand(
        task_id=task_id,
        task_version=1,
        low=0.5,
        high=0.9,
        mean=0.7,
        sample_size=10,
        judge_model=judge_model,
        reason=reason,
    )
    dest = baselines_dir / f"{task_id}.json"
    dest.write_text(band.model_dump_json(indent=2), encoding="utf-8")
    return dest


class TestBaselineCheckCommand:
    def test_current_baseline_ok(self, tmp_path: Path) -> None:
        """A baseline with the current judge model reports OK."""
        baselines_dir = tmp_path / "baselines"
        baselines_dir.mkdir()
        _write_band(baselines_dir, "task-ok", "gpt-5.4-high", "calibration")

        exit_code = main([
            "baseline", "check",
            "--baselines-dir", str(baselines_dir),
            "--judge-model", "gpt-5.4-high",
        ])
        assert exit_code == 0

    def test_stale_judge_model_flagged(self, tmp_path: Path) -> None:
        """A baseline with an old judge model is flagged as STALE."""
        baselines_dir = tmp_path / "baselines"
        baselines_dir.mkdir()
        _write_band(baselines_dir, "task-stale", "gpt-4", "calibration")

        exit_code = main([
            "baseline", "check",
            "--baselines-dir", str(baselines_dir),
            "--judge-model", "gpt-5.4-high",
        ])
        assert exit_code == 1

    def test_mixed_baselines_flagged(self, tmp_path: Path) -> None:
        """One current + one stale: exits 1, stale is flagged."""
        baselines_dir = tmp_path / "baselines"
        baselines_dir.mkdir()
        _write_band(baselines_dir, "task-current", "gpt-5.4-high", "calibration")
        _write_band(baselines_dir, "task-old", "gpt-3.5-turbo", "calibration")

        exit_code = main([
            "baseline", "check",
            "--baselines-dir", str(baselines_dir),
            "--judge-model", "gpt-5.4-high",
        ])
        assert exit_code == 1

    def test_empty_baselines_dir_exits_0(self, tmp_path: Path) -> None:
        """Empty baselines dir has nothing stale."""
        baselines_dir = tmp_path / "baselines"
        baselines_dir.mkdir()

        exit_code = main([
            "baseline", "check",
            "--baselines-dir", str(baselines_dir),
            "--judge-model", "gpt-5.4-high",
        ])
        assert exit_code == 0

    def test_check_requires_no_task_arg(self) -> None:
        """Parser allows 'check' without --task (unlike 'show')."""
        parser = build_parser()
        args = parser.parse_args([
            "baseline", "check",
            "--baselines-dir", "baselines/",
        ])
        assert args.baseline_action == "check"
        assert args.task is None

    def test_show_still_works(self, tmp_path: Path) -> None:
        """The existing 'show' action still functions after adding 'check'."""
        baselines_dir = tmp_path / "baselines"
        baselines_dir.mkdir()
        _write_band(baselines_dir, "my-task", "gpt-5.4-high", "initial")

        exit_code = main([
            "baseline", "show",
            "--task", "my-task",
            "--baselines-dir", str(baselines_dir),
        ])
        assert exit_code == 0


class TestCalibrateReason:
    def test_calibrate_parser_accepts_reason(self) -> None:
        """Parser accepts --reason for calibrate."""
        parser = build_parser()
        args = parser.parse_args([
            "calibrate",
            "--task", "my-task",
            "--reason", "judge_model=gpt-5.4-high prompt_v=1",
            "--judge-stub",
        ])
        assert args.reason == "judge_model=gpt-5.4-high prompt_v=1"

    def test_calibrate_reason_stored_in_band(self, tmp_path: Path) -> None:
        """compute_band respects a custom reason string."""
        band = compute_band(
            [True, True, False, True],
            judge_model="gpt-5.4-high",
            task_id="t1",
            task_version=1,
            reason="judge_model=gpt-5.4-high prompt_v=1",
        )
        assert "judge_model=gpt-5.4-high" in band.reason
        assert "prompt_v=1" in band.reason
