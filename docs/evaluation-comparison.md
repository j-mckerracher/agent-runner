# Baseline Comparison & Regression Classification (Prompt 5)

## Why this exists

Prompts 1-4 gave us a versioned v0.2 evaluation report (`eval/report_schema.py`),
benchmark manifests, and canonical trace events. None of that answers the
question a developer actually asks after a workflow change: *did this
candidate get better or worse than the baseline, and exactly where?*

`eval/comparison.py` compares two v0.2 `EvalReport`s (or dicts) and produces
one canonical, versioned `ComparisonResult` — every AC delta, benchmark
delta, scorecard metric delta, config-drift finding, and a single documented
classification, all computed deterministically with no server, no Opik, no
LLM, and no live workflow execution. `eval/render_comparison.py` turns that
object into a plain-text summary; it never re-derives anything the engine
already decided.

**Deferred to later prompts:** wiring this into `eval/runner.py`'s live
execution path, and any CLI command. This is a pure library today.

## Programmatic usage

```python
from eval.comparison import compare_reports, load_and_compare
from eval.render_comparison import render_summary

# From already-loaded EvalReport objects or plain dicts:
result = compare_reports(baseline_report, candidate_report)

# From report JSON files on disk:
result = load_and_compare("reports/baseline.json", "reports/candidate.json")

print(render_summary(result))
result.to_json()   # canonical machine-readable output
```

`compare_reports` accepts `EvalReport` instances or dicts on either side
(dicts are validated with `eval.report_schema.validate_report_payload`
first; failures raise `CompareInputError` tagged with which side and which
schema errors — this is an input-rejection, never a classification).
Neither input is mutated.

## Output structure (`ComparisonResult`)

- `comparison_schema_version` — independently versioned from the report
  schema; currently `"1.0"`. A comparison result's shape can evolve without
  bumping `report_schema.REPORT_SCHEMA_VERSION`, and vice versa.
- `baseline_identity` / `candidate_identity` — see below.
- `comparability` / `comparability_reasons` — whether the two reports could
  be meaningfully compared at all.
- `drift` — list of `DriftFinding` (informational/material/incompatible).
- `ac_deltas` (+ grouped views: `critical_ac_regressions`,
  `critical_ac_improvements`, `regressed_acs`, `improved_acs`, `added_acs`,
  `removed_acs`, `incomparable_acs`).
- `benchmark_deltas` (+ grouped views: `easy_regressions`, `new_failures`,
  `resolved_failures`, `regressed_benchmarks`, `improved_benchmarks`,
  `added_benchmarks`, `removed_benchmarks`).
- `scorecard_deltas` — `{"capability": {...}, "reliability": {...},
  "efficiency": {...}}`, each a map of metric name to `MetricDelta`.
- `evidence_deltas` (+ `evidence_regressions`) — traceability field deltas.
- `classification` — one of the five `Classification` values.
- `reasons` — ordered list of `ClassificationReason` (stable `code`,
  `severity`, human `summary`, and the specific `benchmark_ids`/`ac_ids`/
  `metric_refs` it's about).
- `policy` — the exact `ComparisonPolicy` snapshot used, so a result is
  self-describing even if defaults change later.
- `warnings` — non-fatal notes (e.g. a case whose trial counts weren't
  comparable).

Every field serializes through `to_dict()`/`to_json()` to plain JSON
primitives only — no enum instances, no dataclasses, no paths, no
datetimes leak through. `validate_comparison_payload()` round-trips a dict
and returns `[]` on a valid canonical payload.

## Identity and drift: fixed fields only

Identity/drift extraction reads **only**:
- Explicit top-level report fields: `eval_run_id`, `created_at`,
  `candidate_version`, `baseline_version`, `benchmark_suite_id`,
  `report_schema_version`.
- A fixed, documented allowlist of `metadata` keys
  (`ComparisonPolicy.identity_metadata_keys`): `runner`, `model`,
  `workflow_config`, `prompt_hash`, `config_hash`, `loop_limit`,
  `target_repo_version`.

The freeform `summary` dict on a report is **never** parsed for identity or
drift — it's narrative, not a contract. An identity field absent from a
report is represented as `None`/absent, never fabricated.

Drift severity for a differing metadata key:
- **material** — the key is in `material_drift_metadata_keys` (by default,
  identical to the full identity allowlist).
- **informational** — the key is in the allowlist but not the material
  subset (there currently are none by default, but the policy supports the
  distinction for future keys).
- **incompatible** — `benchmark_suite_id` differs. This alone drives
  `comparability` to `INCOMPARABLE` and the classification to
  `INCONCLUSIVE` — the two reports aren't measuring the same suite, so no
  other delta in the result should be trusted.

Difficulty tiers (`easy`/`medium`/`hard`) used for AC/benchmark deltas come
**only** from each report's own `benchmark_case_results[].difficulty` field
— the comparison layer never imports `eval/benchmark_manifest.py` to
reconstruct historical difficulty. If the baseline and candidate disagree on
a case's difficulty, both values are carried on the delta independently and
the easy-tier hard gate is evaluated against the **candidate's** difficulty
(the tier a regression would land in today).

## Metric-delta semantics (`MetricDelta`)

A delta is only ever computed when **both** sides report `MetricStatus.known`
— an `unknown`/`not_collected`/`not_applicable` value on either side yields
`availability = UNAVAILABLE` with a `reason`, never a fabricated zero.

**Zero-baseline rule:** when both sides are known and the baseline value is
`0`, `absolute_delta` is still computed normally (`candidate - baseline`).
Only `relative_delta` becomes `None`, with `reason` set to something like
`"relative delta undefined: baseline value is 0 (absolute delta still
computed)"`. `availability` stays `COMPUTED` — a delta with an unavailable
percentage is not the same thing as a delta that couldn't be computed at
all, and callers must be able to tell the two apart.

Boolean-valued scorecard fields (the traceability `*_present` metrics) never
get a numeric delta; they're handled by the separate evidence-tier logic
below.

## Trial-count comparability (two tiers)

1. **Per-case:** a matched benchmark case's `trial_count_comparable` flag is
   `True` only when both sides have `>= policy.min_trials` trials (default
   1) and `max(count) / min(count) <= policy.max_trial_count_ratio` (default
   `2.0`). When the ratio is exceeded, that case's reliability/runtime
   comparison is marked incomparable via a warning — its pass/fail category
   (`ChangeCategory`) is unaffected; only trial-derived numeric comparisons
   for that case are suppressed.
2. **Report-level:** if either report has **zero** recorded trials across
   every benchmark case, the whole comparison is treated as insufficient
   evidence: `comparability = INCOMPARABLE`, classification =
   `INCONCLUSIVE`. There is nothing to compare if one side never ran.

## Evidence tiers: required / conditional / optional

`ComparisonPolicy` splits the five `TraceabilityScore` boolean fields into
three tiers instead of treating every missing evidence reference as an
automatic hard failure:

- **Required** (`trace_present`): losing this (baseline `True`, candidate
  `False`) is a hard gate → `REGRESSED` (`required_evidence_lost`). If
  either side's value for a required field is itself unavailable
  (`unknown`/`not_collected`), the whole comparison is treated as
  insufficient evidence → `INCONCLUSIVE` (`required_evidence_unavailable`) —
  you can't classify a comparison you can't verify was even traced.
- **Conditional** (`test_output_present`, `final_artifact_refs_present`,
  `final_diff_ref_present`): losing one of these is a soft signal → folded
  into `MIXED` (`conditional_evidence_lost`), not a hard gate and not
  `INCONCLUSIVE`.
- **Optional** (`prompt_hashes_present`): losing this is tracked on the
  delta (`degraded`) but never affects classification at all.

Evidence gained (candidate has it, baseline didn't) is tracked
(`evidence_gained`) as an informational reason regardless of tier.

## Classification

Five values: `improved`, `regressed`, `mixed`, `unchanged`, `inconclusive`.
Centralized in `eval.classification.classify()` — `eval.comparison` builds
deltas, `classify()` is the only place that turns them into a verdict, and
`eval.render_comparison` only displays whatever `classify()` decided.

Precedence, evaluated in this exact order:

1. **Input/schema failure** — not handled here; `eval.comparison` raises
   `CompareInputError` before a `ComparisonResult` exists.
2. **Insufficient evidence / non-comparable** → `INCONCLUSIVE`. Triggers:
   `comparability != COMPARABLE` (incompatible suite drift, zero-trials
   report-level rule) or a required evidence field whose value is
   unavailable on either side.
3. **Hard gates** — any one forces `REGRESSED` regardless of any
   improvement elsewhere:
   - `easy_benchmark_regression` — any easy-tier benchmark case that
     regressed.
   - `critical_ac_regression` — any AC marked `critical: true` that
     regressed.
   - `ac_pass_rate_drop` — overall `ac_pass_rate` dropped by
     `>= ac_pass_rate_materiality` (default `0.05`).
   - `workflow_failure_rate_increase` — `workflow_failure_rate` rose by
     `>= workflow_failure_rate_materiality` (default `0.05`).
   - `required_evidence_lost` — a required evidence field regressed.
   - `runtime_hard_budget_breach` / `cost_hard_budget_breach` — wall-clock
     or estimated cost rose `>= runtime_increase_hard_budget_pct` /
     `cost_increase_hard_budget_pct` (default `50%`) relative to baseline.
   - `benchmark_suite_mismatch` — incompatible drift (also drives
     `comparability`, so this and step 2 usually fire together via the same
     underlying condition).
4. **Mixed movement** — material improvements and material regressions both
   present → `MIXED` (`mixed_movement`).
5. **Documented deviation** — material regressions present with **no**
   offsetting improvement, but none severe enough to be a hard gate, still
   yield `MIXED` (`unresolved_material_regression`), never silently
   `UNCHANGED`. An unresolved regression always needs a human look, even
   alone.
6. **Clean improvement** — only material improvements present → `IMPROVED`.
7. **Nothing material** → `UNCHANGED` (`no_material_change`).

Material improvements/regressions below the hard-gate thresholds still
surface as reasons (`ac_pass_rate_gain`, `clean_improvement`,
`reliability_improvement`/`reliability_regression`,
`runtime_soft_budget_breach`, `cost_soft_budget_breach`,
`conditional_evidence_lost`, `material_drift`, etc.) — they just don't by
themselves force `REGRESSED`.

## Default policy (`ComparisonPolicy`)

| Field | Default | Meaning |
|---|---|---|
| `ac_pass_rate_materiality` | `0.05` | 5pp AC-pass-rate drop is a hard gate |
| `workflow_failure_rate_materiality` | `0.05` | 5pp workflow-failure-rate rise is a hard gate |
| `trial_pass_rate_materiality` | `0.10` | 10pp trial-pass-rate move is material (soft) |
| `runtime_increase_soft_budget_pct` | `15.0` | soft warning at +15% wall-clock |
| `runtime_increase_hard_budget_pct` | `50.0` | hard gate at +50% wall-clock |
| `cost_increase_soft_budget_pct` | `15.0` | soft warning at +15% estimated cost |
| `cost_increase_hard_budget_pct` | `50.0` | hard gate at +50% estimated cost |
| `min_trials` | `1` | minimum trials per case for trial-derived comparison |
| `max_trial_count_ratio` | `2.0` | per-case trial-count comparability ratio |
| `required_evidence_fields` | `("trace_present",)` | hard-gate evidence |
| `conditional_evidence_fields` | `("test_output_present", "final_artifact_refs_present", "final_diff_ref_present")` | soft-signal evidence |
| `optional_evidence_fields` | `("prompt_hashes_present",)` | tracked, never gates |
| `identity_metadata_keys` | 7 fixed keys (see above) | identity/drift allowlist |
| `material_drift_metadata_keys` | same 7 keys by default | which drifted keys count as material |

`ComparisonPolicy.validate()` rejects negative thresholds, a hard budget
below its soft counterpart, `min_trials < 1`, `max_trial_count_ratio < 1`,
evidence fields listed in more than one tier, and
`material_drift_metadata_keys` not being a subset of
`identity_metadata_keys`. `PolicyConfigError` is raised on any of these —
never silently coerced to a valid value.

## How the human-readable summary is derived

`render_summary(result)` reads **only** the canonical `ComparisonResult` —
it never re-derives a classification or re-applies a threshold. If the
canonical object says `mixed`, the summary says `MIXED`; rendering is
display, not judgment.

One cosmetic exception exists: `MetricDelta.absolute_delta` /
`relative_delta` are **never rounded** in the canonical object (plain-float
subtraction noise like `0.33299999999999996` is preserved exactly, since
that's the true computed value). The renderer's `_fmt_num()` helper rounds
*only for display* (6 decimal places, trailing-zero trimmed) so a summary
reads `Δ +0.333` instead of the raw float — this is purely cosmetic and does
not affect `to_dict()`/`to_json()` output at all.

## Current limitations

- `comparison_schema.py` provides `to_dict`/`to_json`/`validate_payload` for
  `ComparisonResult`, but no full `from_dict` deserializer back into typed
  dataclasses — round-tripping a comparison result back into live objects
  isn't needed yet (nothing currently re-loads a serialized comparison), so
  it wasn't built speculatively.
- Identity/drift is limited to the fixed field/metadata allowlist above by
  design (see adjustment rationale) — anything living only in a report's
  freeform `summary` text is invisible to comparison, on purpose.
- Live workflow execution and any CLI command (`eval/runner.py --compare`)
  are explicitly deferred — this prompt only builds the library.

## What's next

Prompt 6 wires this comparison layer into live evaluation execution
(`eval/runner.py`), and Prompt 7 is expected to add CLI/GUI surface for it.
Neither is in scope here.
