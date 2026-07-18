"""Tests for eval/benchmark_manifest.py.

No real LLM calls. Uses tmp_path fixtures for manifest and legacy cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.benchmark_manifest import (
    BenchmarkManifest,
    BenchmarkValidationError,
    Oracle,
    ExpectedBaselineBand,
    discover_benchmark_dirs,
    inventory_benchmarks,
    load_and_validate,
    load_and_validate_strict,
    load_manifest,
    validate_manifest,
)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _valid_manifest_dict() -> dict:
    return {
        "id": "BM-001",
        "difficulty": "medium",
        "domain": "widgets",
        "description": "Do the thing.",
        "tags": ["a", "b"],
        "acceptance_criteria": [
            {"id": "AC1", "text": "does the thing", "critical": True},
            {"id": "AC2", "text": "does the other thing", "critical": False},
        ],
        "oracle": {"type": "hidden_tests", "path": "hidden_tests.py"},
        "expected_baseline_band": {"min_ac_pass_rate": 0.4, "max_ac_pass_rate": 0.8},
        "metadata": {"note": "example"},
    }


# --------------------------------------------------------------------------
# Manifest loading
# --------------------------------------------------------------------------


def test_loads_valid_manifest(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    (case_dir / "hidden_tests.py").write_text("AC_TEST_MAP = {}\n", encoding="utf-8")
    _write_json(case_dir / "manifest.json", _valid_manifest_dict())

    manifest, errors = load_and_validate(case_dir)

    assert errors == []
    assert manifest.id == "BM-001"
    assert manifest.difficulty == "medium"
    assert manifest.source == "manifest"
    assert len(manifest.acceptance_criteria) == 2
    assert manifest.oracle.type == "hidden_tests"
    assert manifest.expected_baseline_band.min_ac_pass_rate == 0.4


def test_benchmark_json_filename_also_recognized(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    _write_json(case_dir / "benchmark.json", _valid_manifest_dict())

    manifest = load_manifest(case_dir)

    assert manifest.source == "manifest"
    assert manifest.id == "BM-001"


# --------------------------------------------------------------------------
# Validation failures
# --------------------------------------------------------------------------


def test_rejects_invalid_difficulty(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    data = _valid_manifest_dict()
    data["difficulty"] = "extreme"
    _write_json(case_dir / "manifest.json", data)

    manifest, errors = load_and_validate(case_dir)

    assert any("difficulty" in e for e in errors)
    with pytest.raises(BenchmarkValidationError):
        load_and_validate_strict(case_dir)


def test_rejects_missing_benchmark_id(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    data = _valid_manifest_dict()
    data["id"] = ""
    _write_json(case_dir / "manifest.json", data)

    manifest, errors = load_and_validate(case_dir)

    assert any("id" in e for e in errors)


def test_rejects_duplicate_ac_ids(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    data = _valid_manifest_dict()
    data["acceptance_criteria"] = [
        {"id": "AC1", "text": "first", "critical": False},
        {"id": "AC1", "text": "second", "critical": False},
    ]
    _write_json(case_dir / "manifest.json", data)

    manifest, errors = load_and_validate(case_dir)

    assert any("duplicate" in e.lower() for e in errors)


def test_rejects_missing_ac_text(tmp_path: Path) -> None:
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    data = _valid_manifest_dict()
    data["acceptance_criteria"] = [{"id": "AC1", "text": "", "critical": False}]
    _write_json(case_dir / "manifest.json", data)

    manifest, errors = load_and_validate(case_dir)

    assert any("text" in e for e in errors)


def test_validates_expected_baseline_band_range(tmp_path: Path) -> None:
    manifest = BenchmarkManifest(
        id="BM-1",
        difficulty="easy",
        expected_baseline_band=ExpectedBaselineBand(min_ac_pass_rate=-0.1, max_ac_pass_rate=1.5),
    )
    errors = validate_manifest(manifest)
    assert any("min_ac_pass_rate" in e for e in errors)
    assert any("max_ac_pass_rate" in e for e in errors)


def test_rejects_min_baseline_greater_than_max(tmp_path: Path) -> None:
    manifest = BenchmarkManifest(
        id="BM-1",
        difficulty="easy",
        expected_baseline_band=ExpectedBaselineBand(min_ac_pass_rate=0.9, max_ac_pass_rate=0.2),
    )
    errors = validate_manifest(manifest)
    assert any("min_ac_pass_rate" in e and "max_ac_pass_rate" in e for e in errors)


def test_valid_baseline_band_passes(tmp_path: Path) -> None:
    manifest = BenchmarkManifest(
        id="BM-1",
        difficulty="easy",
        acceptance_criteria=[],
        oracle=Oracle(type="unknown"),
        expected_baseline_band=ExpectedBaselineBand(min_ac_pass_rate=0.2, max_ac_pass_rate=0.6),
    )
    assert validate_manifest(manifest) == []


# --------------------------------------------------------------------------
# Legacy story.json fallback
# --------------------------------------------------------------------------


def _legacy_story() -> dict:
    return {
        "change_id": "EVAL-LEGACY-001",
        "title": "Legacy case",
        "description": "A legacy benchmark case.",
        "acceptance_criteria": [
            "AC1: first criterion text",
            "AC2: second criterion text",
        ],
        "metadata": {
            "difficulty": "hard",
            "critical_acceptance_criteria": ["AC1"],
        },
    }


def test_loads_legacy_story_json(tmp_path: Path) -> None:
    case_dir = tmp_path / "some-case"
    case_dir.mkdir()
    _write_json(case_dir / "story.json", _legacy_story())

    manifest = load_manifest(case_dir)

    assert manifest.source == "legacy"
    assert manifest.id == "EVAL-LEGACY-001"
    assert manifest.description == "A legacy benchmark case."
    assert manifest.difficulty == "hard"
    assert [ac.id for ac in manifest.acceptance_criteria] == ["AC1", "AC2"]
    assert manifest.acceptance_criteria[0].critical is True
    assert manifest.acceptance_criteria[1].critical is False


def test_detects_hidden_tests_py(tmp_path: Path) -> None:
    case_dir = tmp_path / "some-case"
    case_dir.mkdir()
    _write_json(case_dir / "story.json", _legacy_story())
    (case_dir / "hidden_tests.py").write_text("AC_TEST_MAP = {}\n", encoding="utf-8")

    manifest = load_manifest(case_dir)

    assert manifest.oracle.type == "hidden_tests"
    assert manifest.oracle.path == "hidden_tests.py"
    assert validate_manifest(manifest) == []


def test_handles_missing_hidden_tests_honestly(tmp_path: Path) -> None:
    case_dir = tmp_path / "some-case"
    case_dir.mkdir()
    _write_json(case_dir / "story.json", _legacy_story())

    manifest = load_manifest(case_dir)

    assert manifest.oracle.type == "unknown"
    assert manifest.oracle.path is None
    # Not present, but not an error either -- validation should still pass.
    assert validate_manifest(manifest) == []


def test_derives_difficulty_from_directory_name(tmp_path: Path) -> None:
    difficulty_dir = tmp_path / "easy"
    difficulty_dir.mkdir()
    _write_json(
        difficulty_dir / "story.json",
        {
            "change_id": "X",
            "title": "t",
            "description": "d",
            "acceptance_criteria": ["AC1: text"],
            "metadata": {},
        },
    )

    manifest = load_manifest(difficulty_dir)

    assert manifest.difficulty == "easy"


def test_derives_benchmark_id_from_directory_name_when_no_change_id(tmp_path: Path) -> None:
    case_dir = tmp_path / "my-case-dir"
    case_dir.mkdir()
    # No story.json at all -- pure directory-derived id.
    manifest = load_manifest(case_dir)

    assert manifest.id == "my-case-dir"
    assert manifest.source == "legacy"


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


def test_inventory_output_shape(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    root.mkdir()

    easy_dir = root / "easy"
    easy_dir.mkdir()
    _write_json(easy_dir / "story.json", _legacy_story())
    (easy_dir / "hidden_tests.py").write_text("AC_TEST_MAP = {}\n", encoding="utf-8")

    entries = inventory_benchmarks(root)

    assert len(entries) == 1
    entry = entries[0]
    for key in (
        "benchmark_id",
        "path",
        "difficulty",
        "domain",
        "manifest_path",
        "story_path",
        "hidden_test_path",
        "ac_count",
        "validation_status",
        "validation_errors",
        "file_hashes",
    ):
        assert key in entry
    assert entry["ac_count"] == 2
    assert entry["validation_status"] == "valid"
    assert entry["validation_errors"] == []
    assert entry["manifest_path"] is None
    assert entry["hidden_test_path"] is not None


def test_inventory_reports_invalid_status_and_errors(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    root.mkdir()
    bad_dir = root / "weird"
    bad_dir.mkdir()
    data = _valid_manifest_dict()
    data["difficulty"] = "extreme"
    _write_json(bad_dir / "manifest.json", data)

    entries = inventory_benchmarks(root)

    assert len(entries) == 1
    assert entries[0]["validation_status"] == "invalid"
    assert any("difficulty" in e for e in entries[0]["validation_errors"])


def test_discover_benchmark_dirs_missing_root_returns_empty(tmp_path: Path) -> None:
    assert discover_benchmark_dirs(tmp_path / "does-not-exist") == []
