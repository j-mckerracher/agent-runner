# Evaluation Report Contract (v0.2)

## Why this exists

`eval/runner.py` today produces a working but ad hoc report shape
(`eval/result_schema.py::BenchmarkResult`, assembled by `write_report`).
It answers "did the benchmark pass" but has no versioned contract, no
per-acceptance-criteria evidence trail, no distinction between "measured
zero" and "we don't know," and no scorecard. As later work adds benchmark
manifests (Prompt 3), canonical trace events (Prompt 4), and baseline
comparison (Prompt 5), every one of those needs a stable target format to
read and write. `eval/report_schema.py` is that target: a documented,
versioned, machine-readable `EvalReport` schema.

**Prompt 2 does not require `eval/runner.py` to emit this format.** The
runner keeps writing its current report shape unchanged. `report_schema.py`
is additive — a contract other code can adopt incrementally, plus a
best-effort `adapt_legacy_runner_report()` helper for mapping old reports
into the new shape when useful.

## Report hierarchy

```
EvalReport                      (one eval run)
└── benchmark_case_results[]    (one per benchmark, e.g. "easy"/"medium"/"hard")
    ├── trial_results[]         (one per repeated run of that benchmark)
    ├── acceptance_criteria_results[]  (one per AC defined on the benchmark's story)
    └── evidence_references[]  (pointers to what proves the case result)
```

`suite -> case -> trial -> acceptance criteria`, matching how the harness
actually runs things: a suite is a set of benchmark cases, each case can be
run for multiple trials (`--runs N`), and each trial is judged against a
fixed set of acceptance criteria.

## Scorecard categories

`Scorecard` groups metrics into four categories, each a plain object of
named `Metric` values:

- **capability** — did the candidate do the work: `ac_pass_rate`,
  `critical_ac_pass_rate`, `hidden_test_pass_rate`, `full_benchmark_pass_rate`.
- **reliability** — is the result stable across trials: `trial_pass_rate`,
  `variance`, `flaky_case_count`, `workflow_failure_rate`.
- **efficiency** — what did it cost to get there: `wall_clock_duration_ms`,
  `agent_invocation_count`, `loop_iteration_count`, `token_usage`,
  `estimated_cost`.
- **traceability** — can the result be audited after the fact:
  `trace_present`, `test_output_present`, `final_artifact_refs_present`,
  `final_diff_ref_present`, `prompt_hashes_present`.

## Status meanings

**Acceptance criteria (`AcStatus`)**:
- `pass` — evidence shows the criterion was met.
- `fail` — evidence shows the criterion was not met.
- `unknown` — the criterion could not be evaluated (e.g. the check that
  would prove/disprove it never ran).
- `skipped` — deliberately not evaluated for this trial (e.g. gated behind
  a flag that was off).

**Trials (`TrialStatus`)**: `passed`, `failed`, `error` (the trial itself
crashed, independent of the benchmark's pass/fail judgment), `timeout`,
`skipped`, `unknown`.

**Benchmark cases (`CaseStatus`)**: `passed`, `failed`, `partial` (some
trials passed, some didn't), `error`, `skipped`, `unknown`.

`unknown` is always distinct from `fail`/`failed`: a criterion or trial
that could not be checked is not evidence of a defect, and collapsing the
two would let missing coverage masquerade as verified failure (or,
worse, verified success).

## How missing metrics are represented

Every scorecard value and every trial's `duration_ms` is a `Metric`:

```json
{"status": "known", "value": 0}
{"status": "unknown", "unavailable_reason": "workflow event log was not retained"}
{"status": "not_applicable", "unavailable_reason": "only one trial was run"}
{"status": "not_collected", "unavailable_reason": "session metrics collection was not enabled"}
```

`status == "known"` is the only state where `value` is populated —
including with `0`, `0.0`, or `false`, which are legitimate measurements.
Every other status carries `value: null` and, ideally, an
`unavailable_reason` explaining why.

## Why missing values must not be reported as zero

A `token_usage` of `0` and a `token_usage` that was never measured look
identical to a report reader if both are written as `0`. That ambiguity
is exactly what caused (and hid) real gaps in efficiency/traceability data
in the legacy report shape — `cost_usd` there is always `0.0` because
nothing computes it, not because every run is free. `Metric`'s explicit
status makes "we didn't collect this" impossible to confuse with "this
measured as zero," which is a precondition for later baseline comparison
(Prompt 5) trusting deltas between runs instead of silently comparing
noise to noise.

## Validation

`eval.report_schema.validate_report_payload(data: dict) -> list[str]`
checks a raw dict against the contract (missing top-level fields, invalid
status enums, malformed `Metric` payloads, malformed scorecard shape) and
returns every problem found, not just the first. `EvalReport.from_dict()`
and `EvalReport.from_json()` call this internally and raise
`ReportValidationError(errors)` if the list is non-empty.

## Serialization

`EvalReport` supports `to_dict()` / `to_json()` / `from_dict()` /
`from_json()`. Nested dataclasses (`Scorecard`, `BenchmarkCaseResult`,
`TrialResult`, `AcceptanceCriteriaResult`, `Metric`, `Reference`) each
support the same `to_dict()`/`from_dict()` pair.

A worked example lives at `tests/fixtures/eval/v02_report.json` — one
benchmark case, one trial, two acceptance criteria (one pass, one fail),
a full scorecard mixing `known`/`unknown`/`not_applicable`/`not_collected`
metrics, and evidence/artifact references.

## What later prompts build on this

- **Prompt 3 (benchmark manifests)**: implemented in
  `eval/benchmark_manifest.py` — see `docs/benchmark-manifests.md`. Manifests
  describe what benchmark cases *should* exist and their ACs;
  `BenchmarkCaseResult` / `AcceptanceCriteriaResult` are the shape a
  manifest-driven run reports against. `eval/runner.py` is not yet wired to
  either schema.
- **Prompt 4 (canonical trace events)**: `trace_references` /
  `TrialResult.trace_ref` are currently opaque URIs. The trace event
  contract will define what a valid trace looks like and how to check
  `traceability.trace_present` honestly instead of guessing.
- **Prompt 5 (baseline comparison)**: comparing two `EvalReport`s
  requires the "did we actually measure this, or infer it" distinction
  `Metric` provides — diffing two `not_collected` efficiency numbers
  should not be reported as "no change."

## Known limitations left for later prompts

- `adapt_legacy_runner_report()` is a best-effort, lossy mapping from the
  current `eval/runner.py` report shape. It cannot recover per-AC
  criticality, trace references, token/cost data, or variance across
  trials that the legacy format never captured — those come back as
  `not_collected`/`unknown` `Metric`s, not guesses.
- `eval/runner.py` is unchanged and does not emit v0.2 reports. Wiring it
  up (or replacing `write_report`) is out of scope here.
