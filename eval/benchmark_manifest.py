"""Benchmark manifest schema, legacy-fallback loader, validator, inventory.

Formalizes easy/medium/hard benchmark metadata (acceptance criteria, oracle,
expected baseline band) as an explicit, machine-readable manifest, while
preserving existing benchmarks that only have `story.json` + `hidden_tests.py`
(no `manifest.json`/`benchmark.json`). See `docs/benchmark-manifests.md`.

This module does not change `eval/runner.py` behavior. It is additive: a
loader/validator/inventory that can be adopted incrementally. No benchmark is
required to migrate to a manifest file to keep working.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from core.file_hash import hash_file

MANIFEST_FILENAMES: tuple[str, ...] = ("benchmark.json", "manifest.json")
"""Recognized manifest filenames, checked in this order within a case dir.

YAML variants (benchmark.yaml/.yml, manifest.yaml/.yml) are documented as
future work in docs/benchmark-manifests.md — not implemented here because the
repo has no existing PyYAML dependency to reuse.
"""

VALID_DIFFICULTIES: frozenset[str] = frozenset({"easy", "medium", "hard"})

DEFAULT_BENCHMARKS_ROOT = Path(__file__).resolve().parent / "benchmarks"


class BenchmarkValidationError(Exception):
    """Raised when a benchmark manifest fails structural validation.

    Carries the full list of problems found (not just the first one).
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid benchmark manifest")


# --------------------------------------------------------------------------
# Serialization helper (mirrors eval/report_schema.py)
# --------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_jsonable(value) for key, value in obj.items()}
    return obj


# --------------------------------------------------------------------------
# Manifest dataclasses
# --------------------------------------------------------------------------


@dataclass
class AcceptanceCriterion:
    id: str
    text: str
    critical: bool = False


@dataclass
class Oracle:
    type: str
    path: str | None = None
    command: str | None = None


@dataclass
class ExpectedBaselineBand:
    min_ac_pass_rate: float | None = None
    max_ac_pass_rate: float | None = None


@dataclass
class BenchmarkManifest:
    id: str
    difficulty: str
    description: str = ""
    domain: str | None = None
    tags: list[str] = field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = field(default_factory=list)
    oracle: Oracle = field(default_factory=lambda: Oracle(type="unknown"))
    expected_baseline_band: ExpectedBaselineBand | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "legacy"
    case_path: str | None = None
    manifest_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def find_manifest_file(case_dir: Path) -> Path | None:
    for name in MANIFEST_FILENAMES:
        candidate = case_dir / name
        if candidate.is_file():
            return candidate
    return None


def _parse_legacy_ac_string(raw: str, index: int) -> AcceptanceCriterion:
    if ":" in raw:
        ac_id, _, text = raw.partition(":")
        ac_id = ac_id.strip()
        text = text.strip()
    else:
        ac_id, text = "", raw.strip()
    if not ac_id:
        ac_id = f"AC{index + 1}"
    return AcceptanceCriterion(id=ac_id, text=text)


def _load_story_json(case_dir: Path) -> dict[str, Any] | None:
    story_path = case_dir / "story.json"
    if not story_path.is_file():
        return None
    try:
        data = json.loads(story_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _derive_difficulty(case_dir: Path, story: dict[str, Any] | None) -> str:
    if case_dir.parent.name in VALID_DIFFICULTIES:
        return case_dir.parent.name
    if case_dir.name in VALID_DIFFICULTIES:
        return case_dir.name
    if story is not None:
        difficulty = story.get("metadata", {}).get("difficulty")
        if isinstance(difficulty, str) and difficulty:
            return difficulty
    return "unknown"


def _derive_legacy_manifest(case_dir: Path) -> BenchmarkManifest:
    story = _load_story_json(case_dir)
    hidden_tests_path = case_dir / "hidden_tests.py"

    benchmark_id = case_dir.name
    description = ""
    acceptance_criteria: list[AcceptanceCriterion] = []
    metadata: dict[str, Any] = {}

    if story is not None:
        benchmark_id = story.get("change_id") or benchmark_id
        description = story.get("description") or story.get("title") or ""
        metadata = story.get("metadata", {}) if isinstance(story.get("metadata"), dict) else {}
        critical_ids = set(metadata.get("critical_acceptance_criteria") or [])
        raw_acs = story.get("acceptance_criteria")
        if isinstance(raw_acs, list):
            for idx, raw in enumerate(raw_acs):
                if not isinstance(raw, str):
                    continue
                ac = _parse_legacy_ac_string(raw, idx)
                ac.critical = ac.id in critical_ids
                acceptance_criteria.append(ac)

    if hidden_tests_path.is_file():
        oracle = Oracle(type="hidden_tests", path="hidden_tests.py")
    else:
        oracle = Oracle(type="unknown", path=None)

    return BenchmarkManifest(
        id=benchmark_id,
        difficulty=_derive_difficulty(case_dir, story),
        description=description,
        acceptance_criteria=acceptance_criteria,
        oracle=oracle,
        metadata=metadata,
        source="legacy",
        case_path=str(case_dir),
        manifest_path=None,
    )


def _load_manifest_file(case_dir: Path, manifest_path: Path) -> BenchmarkManifest:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkValidationError([f"{manifest_path}: could not parse manifest JSON: {exc}"])
    if not isinstance(data, dict):
        raise BenchmarkValidationError([f"{manifest_path}: manifest must be a JSON object"])

    acs_raw = data.get("acceptance_criteria") or []
    acceptance_criteria = [
        AcceptanceCriterion(
            id=str(item.get("id", "")),
            text=str(item.get("text", "")),
            critical=bool(item.get("critical", False)),
        )
        for item in acs_raw
        if isinstance(item, dict)
    ]

    oracle_raw = data.get("oracle") or {}
    oracle = Oracle(
        type=oracle_raw.get("type", "unknown") if isinstance(oracle_raw, dict) else "unknown",
        path=oracle_raw.get("path") if isinstance(oracle_raw, dict) else None,
        command=oracle_raw.get("command") if isinstance(oracle_raw, dict) else None,
    )

    band_raw = data.get("expected_baseline_band")
    band = None
    if isinstance(band_raw, dict):
        band = ExpectedBaselineBand(
            min_ac_pass_rate=band_raw.get("min_ac_pass_rate"),
            max_ac_pass_rate=band_raw.get("max_ac_pass_rate"),
        )

    return BenchmarkManifest(
        id=data.get("id", case_dir.name),
        difficulty=data.get("difficulty", "unknown"),
        description=data.get("description", ""),
        domain=data.get("domain"),
        tags=list(data.get("tags") or []),
        acceptance_criteria=acceptance_criteria,
        oracle=oracle,
        expected_baseline_band=band,
        metadata=data.get("metadata") or {},
        source="manifest",
        case_path=str(case_dir),
        manifest_path=str(manifest_path),
    )


def load_manifest(case_dir: Path) -> BenchmarkManifest:
    """Load a benchmark's manifest, falling back to legacy story.json derivation."""
    manifest_path = find_manifest_file(case_dir)
    if manifest_path is not None:
        return _load_manifest_file(case_dir, manifest_path)
    return _derive_legacy_manifest(case_dir)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_manifest(manifest: BenchmarkManifest) -> list[str]:
    """Return a list of validation error strings; empty list means valid."""
    errors: list[str] = []

    if not manifest.id:
        errors.append("benchmark id is missing")

    if manifest.difficulty not in VALID_DIFFICULTIES:
        errors.append(
            f"difficulty '{manifest.difficulty}' is invalid; must be one of {sorted(VALID_DIFFICULTIES)}"
        )

    seen_ids: set[str] = set()
    for ac in manifest.acceptance_criteria:
        if not ac.id:
            errors.append("acceptance criterion is missing an id")
        elif ac.id in seen_ids:
            errors.append(f"duplicate acceptance criterion id '{ac.id}'")
        else:
            seen_ids.add(ac.id)
        if not ac.text:
            errors.append(f"acceptance criterion '{ac.id or '?'}' is missing text")
        if not isinstance(ac.critical, bool):
            errors.append(f"acceptance criterion '{ac.id or '?'}' critical flag must be boolean")

    if manifest.oracle is None:
        errors.append("oracle is missing (use type 'unknown' if not yet defined)")
    else:
        if not manifest.oracle.type:
            errors.append("oracle type is missing")
        if manifest.oracle.type == "hidden_tests":
            if not manifest.oracle.path:
                errors.append("oracle type 'hidden_tests' requires a path")
            elif manifest.case_path is not None:
                hidden_path = Path(manifest.case_path) / manifest.oracle.path
                if not hidden_path.is_file():
                    errors.append(f"oracle path '{manifest.oracle.path}' does not exist in {manifest.case_path}")

    band = manifest.expected_baseline_band
    if band is not None:
        for field_name, value in (
            ("min_ac_pass_rate", band.min_ac_pass_rate),
            ("max_ac_pass_rate", band.max_ac_pass_rate),
        ):
            if value is not None and not (0.0 <= value <= 1.0):
                errors.append(f"expected_baseline_band.{field_name} must be between 0.0 and 1.0, got {value}")
        if (
            band.min_ac_pass_rate is not None
            and band.max_ac_pass_rate is not None
            and band.min_ac_pass_rate > band.max_ac_pass_rate
        ):
            errors.append(
                "expected_baseline_band.min_ac_pass_rate must not be greater than max_ac_pass_rate "
                f"({band.min_ac_pass_rate} > {band.max_ac_pass_rate})"
            )

    return errors


def load_and_validate(case_dir: Path) -> tuple[BenchmarkManifest, list[str]]:
    """Load a manifest (with legacy fallback) and validate it without raising."""
    manifest = load_manifest(case_dir)
    return manifest, validate_manifest(manifest)


def load_and_validate_strict(case_dir: Path) -> BenchmarkManifest:
    """Like `load_and_validate` but raises `BenchmarkValidationError` on failure."""
    manifest, errors = load_and_validate(case_dir)
    if errors:
        raise BenchmarkValidationError(errors)
    return manifest


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


def discover_benchmark_dirs(root: Path = DEFAULT_BENCHMARKS_ROOT) -> list[Path]:
    """Discover benchmark case directories under `root` (one level of subdirs)."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _file_hashes_for_case(case_dir: Path, manifest: BenchmarkManifest) -> dict[str, str]:
    hashes: dict[str, str] = {}
    story_path = case_dir / "story.json"
    if story_path.is_file():
        hashes["story.json"] = hash_file(story_path)
    if manifest.manifest_path is not None:
        manifest_name = Path(manifest.manifest_path).name
        hashes[manifest_name] = hash_file(Path(manifest.manifest_path))
    if manifest.oracle is not None and manifest.oracle.type == "hidden_tests" and manifest.oracle.path:
        hashes[manifest.oracle.path] = hash_file(case_dir / manifest.oracle.path)
    return hashes


def inventory_benchmarks(root: Path = DEFAULT_BENCHMARKS_ROOT) -> list[dict[str, Any]]:
    """Produce a machine-readable inventory of discovered benchmarks."""
    entries: list[dict[str, Any]] = []
    for case_dir in discover_benchmark_dirs(root):
        manifest, errors = load_and_validate(case_dir)
        story_path = case_dir / "story.json"
        hidden_tests_path = case_dir / "hidden_tests.py"
        entries.append(
            {
                "benchmark_id": manifest.id,
                "path": str(case_dir),
                "difficulty": manifest.difficulty,
                "domain": manifest.domain,
                "manifest_path": manifest.manifest_path,
                "story_path": str(story_path) if story_path.is_file() else None,
                "hidden_test_path": str(hidden_tests_path) if hidden_tests_path.is_file() else None,
                "ac_count": len(manifest.acceptance_criteria),
                "validation_status": "valid" if not errors else "invalid",
                "validation_errors": errors,
                "file_hashes": _file_hashes_for_case(case_dir, manifest),
            }
        )
    return entries


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.benchmark_manifest")
    parser.add_argument("command", choices=["inventory"], help="Command to run")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_BENCHMARKS_ROOT,
        help="Benchmarks root directory (default: eval/benchmarks)",
    )
    args = parser.parse_args(argv)

    if args.command == "inventory":
        print(json.dumps(inventory_benchmarks(args.root), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
