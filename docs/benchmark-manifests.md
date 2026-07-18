# Benchmark Manifests

## Why this exists

Benchmark cases (`eval/benchmarks/{easy,medium,hard}/`) currently carry their
metadata only inside `story.json` — free-text `acceptance_criteria` strings,
an implicit oracle (`hidden_tests.py`, if present), and difficulty inferred
from the directory name. That's workable but not formally validated: nothing
enforces that AC ids are unique, that difficulty is one of a fixed set, or
that a declared oracle path actually exists.

`eval/benchmark_manifest.py` adds an explicit, machine-readable manifest
schema for this metadata, a validator, and an inventory command — without
requiring every existing benchmark to migrate. Benchmarks with no manifest
file keep working via legacy derivation from `story.json` +
`hidden_tests.py`.

**This task does not wire the new schema into `eval/runner.py`.** The runner
keeps loading benchmarks the way it always has
(`benchmark_story`/`benchmark_ac_test_map`/`discover_benchmarks` in
`eval/runner.py`). The manifest module is additive and separate, in the same
spirit as `eval/report_schema.py` (see `docs/evaluation-contract.md`).

## Supported difficulty tiers

Exactly three values are valid: `easy`, `medium`, `hard`. Anything else fails
validation with a structured error (not a print statement).

## Manifest format

A case directory may contain one of these files (checked in this order):

- `benchmark.json`
- `manifest.json`

YAML variants (`benchmark.yaml`/`.yml`, `manifest.yaml`/`.yml`) are **not**
implemented yet — the repo has no existing PyYAML dependency, and adding one
solely for this format wasn't judged worth the new dependency. This is
documented future work.

### Fields

```jsonc
{
  "id": "EVAL-EASY-001",
  "difficulty": "easy",              // easy | medium | hard
  "domain": "docs-generation",       // optional
  "description": "...",
  "tags": ["obsidian", "recap"],     // optional
  "acceptance_criteria": [
    { "id": "AC1", "text": "...", "critical": true },
    { "id": "AC2", "text": "...", "critical": false }
  ],
  "oracle": {
    "type": "hidden_tests",         // or "command", or "unknown"
    "path": "hidden_tests.py",
    "command": null
  },
  "expected_baseline_band": {
    "min_ac_pass_rate": 0.5,
    "max_ac_pass_rate": 0.9
  },
  "metadata": {}
}
```

### Example

See `eval/benchmarks/easy/story.json` for the legacy equivalent of the
fields above; an explicit manifest for that case would look like the JSON
snippet shown.

## Legacy fallback behavior

If a case directory has no `benchmark.json`/`manifest.json`,
`eval.benchmark_manifest.load_manifest()` derives a `BenchmarkManifest` from:

- **id** — `story.json["change_id"]`, else the directory name.
- **difficulty** — the case directory's own name if it is `easy`/`medium`/
  `hard` (current layout: `eval/benchmarks/easy/`), else the parent
  directory's name if *that* is one of the three tiers (future nested
  layout: `eval/benchmarks/easy/some-case/`), else
  `story.json["metadata"]["difficulty"]`, else `"unknown"` (which then fails
  validation rather than silently guessing).
- **description** — `story.json["description"]` (falls back to `"title"`).
- **acceptance_criteria** — parsed from `story.json["acceptance_criteria"]`
  strings of the form `"AC1: text..."` (split on the first `:`); an AC id is
  marked `critical` if it appears in
  `story.json["metadata"]["critical_acceptance_criteria"]`.
- **oracle** — `{"type": "hidden_tests", "path": "hidden_tests.py"}` if that
  file exists in the case directory, else `{"type": "unknown"}`. A benchmark
  with no `hidden_tests.py` is not an error at load time — it's reported
  honestly as an unknown oracle, and validation only complains if the oracle
  is entirely missing (a `None`), not if it's explicitly `unknown`.

## How to add a new benchmark

1. Create the case directory as usual.
2. Either:
   - Add a `manifest.json` (or `benchmark.json`) with the fields above, or
   - Keep using `story.json` + `hidden_tests.py` — no manifest required.
3. Validate it (see below) before relying on it in an eval run.

## How to validate benchmark metadata

```python
from pathlib import Path
from eval.benchmark_manifest import load_and_validate, load_and_validate_strict

manifest, errors = load_and_validate(Path("eval/benchmarks/easy"))
# errors == [] means valid

# Or raise BenchmarkValidationError(errors) on failure:
manifest = load_and_validate_strict(Path("eval/benchmarks/easy"))
```

Validation checks:

- benchmark id present
- difficulty is one of `easy`/`medium`/`hard`
- each acceptance criterion has a non-empty id, unique among the case's ACs
- each acceptance criterion has non-empty text
- `critical` is a boolean
- oracle is present (an explicit `type: "unknown"` is fine; a missing oracle
  object is not)
- when oracle type is `hidden_tests`, its `path` exists on disk
- `expected_baseline_band` min/max are each in `[0.0, 1.0]` when set
- `expected_baseline_band.min_ac_pass_rate` is not greater than
  `max_ac_pass_rate`

## How to inventory benchmarks

```bash
python -m eval.benchmark_manifest inventory
python -m eval.benchmark_manifest inventory --root path/to/other/benchmarks
```

Prints a JSON array to stdout. Each entry: `benchmark_id`, `path`,
`difficulty`, `domain`, `manifest_path` (or `null` if legacy-derived),
`story_path`, `hidden_test_path`, `ac_count`, `validation_status`
(`"valid"`/`"invalid"`), `validation_errors`, and `file_hashes` (SHA-256 via
`core.file_hash.hash_file`, reused from Prompt 1 rather than re-implemented).

The same logic is available as `eval.benchmark_manifest.inventory_benchmarks(root)`
for programmatic use.

## `expected_baseline_band` usage

This field is **informational** at this stage: it records the AC pass-rate
range a benchmark's author expects a competent agent to land in. It is
validated for internal consistency (range bounds, min ≤ max) but nothing in
this task *enforces* or *compares against* it. Baseline comparison against
this band is Prompt 5's job — this task only makes the field expressible and
well-formed ahead of time.

## Limitations before later v0.2 integration

- `eval/runner.py` is unchanged. It does not read `benchmark_manifest.py` and
  does not emit `eval/report_schema.py`'s v0.2 `EvalReport` shape. Both are
  future wiring work.
- `scripts/capture_v01_baseline.py` still uses its own original legacy-only
  `discover_benchmarks()`/`build_manifest()` for baseline capture; it has not
  been changed to use `eval.benchmark_manifest.inventory_benchmarks()`. The
  richer manifest-based inventory can be wired into baseline capture in a
  later task without destabilizing the existing baseline output.
- No canonical trace event contract yet (Prompt 4).
- No baseline comparison against `expected_baseline_band` yet (Prompt 5).
- YAML manifest support is not implemented (see above).
- Nested case-directory layouts (`eval/benchmarks/<difficulty>/<case>/`) are
  supported by the difficulty-derivation logic but no such cases exist yet;
  today's benchmarks are one case per difficulty directory.
