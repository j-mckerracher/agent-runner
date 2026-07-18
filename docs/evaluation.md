# Evaluation CLI Guide (v0.2)

This is the primary, task-oriented guide to the v0.2 evaluation command
surface: `python -m eval.cli <run|baseline|compare|ci>`. It closes the gap
Prompts 1–6 left open — those prompts built the report contract, benchmark
manifests, canonical traces, and the comparison/classification engine as
libraries; this document (and `eval/cli.py`) is the thin CLI wrapper that
lets a developer or a CI pipeline drive baseline → candidate → compare → gate
without importing Python internals.

For deeper background on the underlying contracts, see:

- [`evaluation-contract.md`](evaluation-contract.md) — the `EvalReport` v0.2
  schema itself (scorecard categories, `Metric` honesty-flag semantics,
  case/trial/AC hierarchy).
- [`benchmark-manifests.md`](benchmark-manifests.md) — how benchmarks are
  authored and discovered (legacy `story.json`+`hidden_tests.py` shape and
  the newer manifest shape).
- [`tracing.md`](tracing.md) — the local JSONL trace contract
  (`telemetry/events.py`), what a `trace.jsonl` file contains, and how it's
  produced during a run.
- [`evaluation-comparison.md`](evaluation-comparison.md) — the comparison
  engine itself (`eval/comparison.py`, `eval/classification.py`): drift
  findings, `Comparability`, `Classification`, and the default policy.
- [`live-eval-integration.md`](live-eval-integration.md) — how a live
  evaluation run wires into `eval/live_report.py` to build a v0.2 report from
  real workflow execution.

Everything below is orchestration on top of those contracts. **The CLI never
reimplements scoring, comparison, or classification logic** — it translates
flags into calls against `eval/runner.py`, `eval/comparison.py`, and
`eval/render_report.py`/`eval/render_comparison.py`, and decides exit codes
via `eval/exit_codes.py`.

## Commands at a glance

| Command | Purpose | Produces |
|---|---|---|
| `run` | Run a candidate evaluation | a v0.2 `EvalReport` (`<output-dir>/latest.json`) |
| `baseline` | Run an evaluation and save it as a baseline | the same, plus a copy at `--output` |
| `compare` | Compare two existing reports (inspection only, non-gating) | a `ComparisonResult` JSON |
| `ci` | Compare two existing reports and gate on the result | the same, plus a process exit code CI can act on |

There is no `baseline select` subcommand. Selecting an *existing* report as
your baseline needs nothing more than pointing `--baseline` at its path —
`compare`/`ci` load and validate it the same way either way. `baseline` exists
only to *generate* a fresh report and save it in one step.

## Workflow 1 — generate a baseline

```bash
python -m eval.cli baseline \
  --suite tests/fixtures/eval/suite \
  --trials 3 \
  --output-dir .tmp/v02-baseline \
  --repo /path/to/repo-under-test \
  --sha <baseline-commit-sha> \
  --output baselines/v0.2-main.json
```

This runs the suite exactly the way `run` does (see below), then copies the
final validated bytes of the produced `latest.json` to `--output` —
never re-serialized by hand, so nothing about the report (including its
evidence-relative paths) diverges from what was actually written. The human
summary (`eval/render_report.py::render_eval_summary`) is printed to stdout
for a quick sanity check.

## Workflow 2 — run a candidate

```bash
python -m eval.cli run \
  --suite tests/fixtures/eval/suite \
  --benchmark legacy-case --benchmark manifest-case \
  --trials 2 \
  --output-dir .tmp/v02-candidate \
  --repo /path/to/repo-under-test \
  --sha <candidate-commit-sha>
```

Flags map onto `eval/runner.py`'s own CLI:

| `eval.cli run` flag | `eval.runner` flag | Notes |
|---|---|---|
| `--suite` | `--benchmarks-dir` | benchmark suite root |
| `--benchmark` (repeatable) | `--benchmark` | omit to run every discovered benchmark |
| `--difficulty` | `--difficulty` | `easy`/`medium`/`hard`, space-separated |
| `--trials` | `--runs` | trials per benchmark |
| `--output-dir` | `--reports-dir` | where the report is written |
| `--repo` / `--sha` | `--repo` / `--sha` | identifies the candidate under test |
| `--runner` / `--model` | `--runner` / `--model` | forwarded as-is |

`run` calls `eval.runner.main(argv)` directly — it does not call
`discover_benchmarks`/`run_one`/`write_report` itself, so orchestration
ownership stays entirely inside `eval/runner.py`. One invocation of
`eval.runner.main` always produces exactly one report: `write_eval_report`
writes both a timestamped file and an identical-bytes `<output-dir>/latest.json`
as part of the same atomic publish, and that fixed filename is what the CLI
reads back — no glob or timestamp reconstruction needed.

## Workflow 3 — compare (inspection only)

```bash
python -m eval.cli compare \
  --baseline baselines/v0.2-main.json \
  --candidate .tmp/v02-candidate/latest.json \
  --output .tmp/v02-comparison.json
```

`compare` is meant for local inspection: it always writes the comparison
JSON and prints a human summary, and treats both `mixed` and `inconclusive`
classifications as non-gating — only genuine incompatibility between the two
reports (see the exit-code table below) causes a nonzero exit without any
extra flag. Use `ci` when you need policy-driven gating.

## Workflow 4 — CI gate

```bash
python -m eval.cli ci \
  --baseline baselines/v0.2-main.json \
  --candidate .tmp/v02-candidate/latest.json \
  --comparison-output .tmp/v02-ci-comparison.json
```

Optional flags:

- `--allow-inconclusive` — treat an `inconclusive` classification as
  non-gating (default: **gating**, exit 6).
- `--allow-mixed` / `--no-allow-mixed` — treat a `mixed` classification as
  non-gating (default: **`--allow-mixed`, i.e. non-gating**).

**The comparison JSON is always written before the exit code is computed.**
A hard-gate failure (exit 6) never means the evidence was discarded — the
comparison file, the human summary, and every evidence/trace reference on
both sides remain exactly where they were written. This is deliberate: a
failing CI job should always leave behind the artifact a human needs to
understand *why* it failed.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Operation completed successfully and no configured hard-regression gate failed. |
| 2 | Invalid CLI usage, invalid input, or invalid configuration. |
| 3 | Evaluation execution failed before a valid canonical report could be completed. |
| 4 | Canonical report generation or report validation failed. |
| 5 | Comparison could not be completed because reports were incompatible or required data was missing. |
| 6 | Candidate was classified as regressed, or another configured hard gate failed. |

These are defined once, centrally, in `eval/exit_codes.py` — no command
computes its own ad hoc exit code. The mapping is grounded directly in the
comparison contract:

- `CompareInputError(side="policy")` → 2 (bad `--allow-*`/config combination).
- `CompareInputError(side="baseline"|"candidate")` → 4 (a report file itself
  failed to load or validate).
- `result.comparability != Comparability.COMPARABLE` → 5 — this is the real
  "incompatible reports" case: mismatched `benchmark_suite_id` between
  baseline and candidate, zero recorded trials on either side, or zero
  shared `benchmark_id`s between the two reports.
- Otherwise, by `result.classification`: `regressed` → 6 always;
  `inconclusive` → 6 unless `--allow-inconclusive`; `mixed` → 0 unless
  `--no-allow-mixed`; `improved`/`unchanged` → 0.

`run`/`baseline` use a narrower slice of the same codes:

- 2 — bad CLI usage caught before `eval.runner.main` is even called (e.g.
  `--trials 0`, a missing required path).
- 3 — `eval.runner.main` itself failed (e.g. no matching benchmark
  discovered) and never produced a `latest.json`.
- 4 — `eval.runner.main` returned success but `latest.json` is missing or
  fails schema validation on reload. This should not happen given the
  contract, but keeps report-generation failures distinguishable from
  eval-execution failures.

The disambiguation between 3 and 4 is intentionally based on whether a
valid `latest.json` was actually written, not on `eval.runner.main`'s raw
return code — that return code also reflects normal report content (some
benchmark cases failed), which is not a CLI-level failure at all and still
exits 0.

## Evidence inspection

Every evaluation run leaves behind more than the JSON report. Under each
trial's evidence directory (rooted at `<output-dir>/evidence/<eval_run_id>/...`,
written by `eval/runner.py`'s `_evidence_dir` during `run_one`):

- `story.json` — the benchmark's story/spec as given to the workflow for
  that trial (`evidence["story_ref"]`).
- `trace.jsonl` — the canonical local JSONL trace for that trial, written
  incrementally by `telemetry.events.JsonlEventSink` for the whole trial
  duration (`evidence["trace_ref"]`). This is unconditional — it does not
  depend on the workflow invocation succeeding, and it's what
  `EvalReport.trace_references` on the final report always points at.
- workflow stdout/stderr — captured from the (real or mocked) workflow
  invocation (`evidence["workflow_result_ref"]`).
- a JUnit XML hidden-test report (`evidence["hidden_tests_xml_ref"]`) — only
  present when hidden tests actually ran against a real cloned workspace
  (`.awb-hidden-tests.xml` inside the workspace).
- a final `git diff` against the workspace (`evidence["diff_ref"]`) — only
  present when a real cloned workspace exists at the end of the trial.

At the report level, `EvalReport.trace_references` is the reliable,
always-populated list of trace pointers across every trial in the run —
each `Reference.uri` resolves to a `trace.jsonl` file on disk. Per-case
`BenchmarkCaseResult.evidence_references` is narrower: it only promotes
`hidden_tests_xml_ref` and `diff_ref` (both of which require a real
subprocess-executed workspace), so it will be empty in test/dry-run
configurations that mock workflow execution.

## Authoring a benchmark

Benchmarks live under a suite root (the `--suite`/`--benchmarks-dir`
directory) as one subdirectory per benchmark, discovered by
`eval/runner.py::discover_benchmarks`. Two shapes are supported side by side
— see [`benchmark-manifests.md`](benchmark-manifests.md) for the full
manifest field reference:

- **Legacy** (still fully supported, not being phased out by this closure):
  `story.json` + `hidden_tests.py` directly in the benchmark directory. See
  `tests/fixtures/eval/suite/legacy-case/` for a minimal working example.
- **Manifest-based**: a `manifest.json` (or `benchmark.json`) alongside
  `story.json`/`hidden_tests.py`, adding a stable `id`, `difficulty`,
  per-acceptance-criteria metadata (including which ACs are `critical`),
  oracle configuration, and baseline bands. See
  `tests/fixtures/eval/suite/manifest-case/` for a minimal working example.

Both shapes run through the exact same `eval.cli run`/`baseline` command
path — scenario coverage for both lives in
`tests/test_v02_end_to_end.py`.

## Missing-metric semantics

Every `Metric` in a `Scorecard` carries an honesty flag
(`eval/report_schema.py::MetricStatus`), and both `eval/render_report.py`
and `eval/render_comparison.py` respect it strictly:

- `known` — `value` holds a real measurement, which may legitimately be `0`.
- `unknown` — the metric was expected but could not be determined.
- `not_applicable` — the metric does not apply to this result at all.
- `not_collected` — instrumentation for this metric was not enabled for
  this run (e.g. token/cost accounting turned off).

A metric that is not `known` is always rendered as
`unavailable (<status>: <reason>)` — **never** silently coerced to `0` or
omitted. This matters most for `token_usage`, `estimated_cost`, and
`wall_clock_duration_ms`: a `0` in one of those fields is a real
measurement of zero cost/time, not "we don't know." If you see `unavailable`
where you expected a number, that means the corresponding instrumentation
was not wired up for that run — not that the value was actually zero.

## Working without Opik or the server

Opik reporting and the `server` package are both optional integrations.
None of `eval/cli.py`, `eval/render_report.py`, `eval/render_comparison.py`,
or the code paths `run`/`baseline`/`compare`/`ci` actually exercise import
either module at load time or anywhere reachable from `main()`. This is
enforced by `tests/test_cli_isolation.py` (module-import isolation, checked
in fresh subprocesses to avoid session-level import-cache pollution) and by
`tests/test_v02_end_to_end.py`'s scenarios 19/20 (full command invocations
run with `opik`/`server` imports actively blocked). You can run the entire
CLI surface — baseline, candidate, compare, CI gating — in an environment
that has neither package installed.

## Local, LLM-free verification

None of the tests above make a real LLM call or a real Opik/server network
call. The full targeted suite:

```bash
python -m pytest \
  tests/test_report_schema.py tests/test_benchmark_manifest.py \
  tests/test_trace_events.py tests/test_eval_comparison.py \
  tests/test_eval_runner.py tests/test_eval_cli.py \
  tests/test_v02_end_to_end.py tests/test_cli_isolation.py \
  tests/test_render_report.py tests/test_v02_acceptance_audit.py -q
```

The E2E determinism guarantee comes specifically from **in-process**
invocation: `tests/test_v02_end_to_end.py` calls `eval.cli.main(argv)`
directly under pytest, with `unittest.mock.patch.object` seams on
`eval.runner.prepare_workspace`/`invoke_workflow`/`run_hidden_tests`. A
`python -m eval.cli run ...` *shell* invocation is a separate OS process
that those monkeypatches cannot reach — it's useful as a manual smoke test
against a real workflow runner, but it is not what gates CI.
