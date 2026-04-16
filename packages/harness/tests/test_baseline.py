"""Tests for the baseline band manager and regression classifier."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner_harness.baseline.manager import compute_band, load_band, save_band
from agent_runner_harness.baseline.regression import classify
from agent_runner_shared.models import BaselineBand


class TestComputeBand:
    def test_single_pass(self) -> None:
        """With 1 pass, mean=1.0, high==low (sample_size < 2)."""
        band = compute_band([True], judge_model="gpt-5.4-high", task_id="t1", task_version=1)
        assert band.mean == 1.0
        assert band.low == band.high == 1.0
        assert band.sample_size == 1

    def test_single_fail(self) -> None:
        """With 1 fail, mean=0.0, high==low (sample_size < 2)."""
        band = compute_band([False], judge_model="gpt-5.4-high", task_id="t1", task_version=1)
        assert band.mean == 0.0
        assert band.low == band.high == 0.0

    def test_mixed_sample(self) -> None:
        """With mixed results, mean = fraction passed, band = mean ± 0.15."""
        # 4 out of 5 pass → mean = 0.8
        band = compute_band(
            [True, True, True, True, False],
            judge_model="gpt-5.4-high",
            task_id="t1",
            task_version=1,
        )
        assert abs(band.mean - 0.8) < 1e-9
        assert abs(band.low - 0.65) < 1e-9
        assert abs(band.high - 0.95) < 1e-9

    def test_clamps_to_zero_one(self) -> None:
        """Band is clamped to [0, 1] even when mean ± 0.15 exceeds bounds."""
        band = compute_band(
            [True, True, True, True, True],
            judge_model="gpt-5.4-high",
            task_id="t1",
            task_version=1,
        )
        assert band.mean == 1.0
        assert band.high == 1.0  # clamped at 1.0
        assert band.low == 0.85

    def test_float_pass_rates(self) -> None:
        """Accepts float pass rates in [0, 1]."""
        band = compute_band(
            [0.5, 0.7, 0.9],
            judge_model="gpt-5.4-high",
            task_id="t1",
            task_version=1,
        )
        expected_mean = (0.5 + 0.7 + 0.9) / 3
        assert abs(band.mean - expected_mean) < 1e-9

    def test_empty_list(self) -> None:
        """Empty pass_rates produces mean=0 with sample_size=0."""
        band = compute_band([], judge_model="gpt-5.4-high", task_id="t1", task_version=1)
        assert band.mean == 0.0
        assert band.sample_size == 0

    def test_reason_stored(self) -> None:
        """Custom reason is stored in the band."""
        band = compute_band(
            [True], judge_model="gpt-5.4-high", task_id="t1", task_version=1,
            reason="my-reason"
        )
        assert band.reason == "my-reason"


class TestSaveAndLoadBand:
    def test_round_trip(self, tmp_path: Path) -> None:
        """Save a band and load it back."""
        band = compute_band(
            [True, False, True],
            judge_model="gpt-5.4-high",
            task_id="my-task",
            task_version=1,
        )
        dest = save_band(band, tmp_path)
        assert dest.exists()
        assert dest.name == "my-task.json"

        loaded = load_band(tmp_path, "my-task")
        assert loaded is not None
        assert loaded.task_id == "my-task"
        assert abs(loaded.mean - band.mean) < 1e-9

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        """load_band returns None for a task with no saved band."""
        result = load_band(tmp_path, "no-such-task")
        assert result is None

    def test_creates_directory(self, tmp_path: Path) -> None:
        """save_band creates baselines_dir if it doesn't exist."""
        new_dir = tmp_path / "nested" / "baselines"
        band = compute_band([True], judge_model="m", task_id="t", task_version=1)
        save_band(band, new_dir)
        assert new_dir.exists()


class TestClassify:
    def _make_band(self, low: float, high: float, sample_size: int = 5) -> BaselineBand:
        return BaselineBand(
            task_id="t",
            task_version=1,
            low=low,
            high=high,
            mean=(low + high) / 2,
            sample_size=sample_size,
            judge_model="m",
        )

    def test_regressed(self) -> None:
        band = self._make_band(0.6, 0.9)
        assert classify(0.4, band) == "regressed"

    def test_improved(self) -> None:
        band = self._make_band(0.6, 0.9)
        assert classify(1.0, band) == "improved"

    def test_unchanged(self) -> None:
        band = self._make_band(0.6, 0.9)
        assert classify(0.75, band) == "unchanged"

    def test_none_band_returns_insufficient(self) -> None:
        assert classify(0.5, None) == "insufficient_data"

    def test_small_sample_returns_insufficient(self) -> None:
        band = self._make_band(0.6, 0.9, sample_size=1)
        assert classify(0.5, band) == "insufficient_data"

    def test_boundary_at_low(self) -> None:
        band = self._make_band(0.6, 0.9)
        assert classify(0.6, band) == "unchanged"

    def test_boundary_just_below_low(self) -> None:
        band = self._make_band(0.6, 0.9)
        assert classify(0.599, band) == "regressed"
