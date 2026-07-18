# Live Eval Integration (Prompt 6)

This document describes how a live `eval/runner.py` invocation produces a
v0.2 evaluation report (`eval/report_schema.py`), what evidence it persists
locally, and what changed for existing report readers.

It is the runtime companion to
[`docs/evaluation-comparison.md`](evaluation-comparison.md) (comparing two
finished reports) and [`eval/README.md`](../eval/README.md) (day-to-day
runner usage).

## Compatibility break — read this first

As of this change, the **v0.2 report is the sole machine report** the runner
writes. There is no dual-write of the old ad hoc JSON shape (`results[]`,
top-level `quality`/`metrics`/`summary` keys) alongside it. Any tool that
parsed the previous report shape directly needs to move to
`eval/report_schema.py`'s `EvalReport`/`validate_report_payload`.

This is intentional, not an oversight — the old shape zero-coerced missing
metrics (`cost_usd: 0.0` when no cost model was configured) and had no
identity/versioning contract. The blast radius was deliberately bounded to
one place: `server/evaluate.py` is now a **thin adapter** that reads the v0.2
report and maps it onto the local GUI's existing response keys. The GUI's
JavaScript is untouched — it still gets the fields it always got, just
sourced from the new report.

## What actually runs, in order

Per trial, `eval/runner.py::run_one` does the same workflow steps it always
did (sandbox → clone+checkout → gold-master verification → workflow →
optional project tests → hidden tests → cleanup). What's new is what happens
around those steps:

1. **Trace events.** If `--reports-dir`/an eval run id is active, a
   `JsonlEventSink` is opened at
   `<reports-dir>/evidence/<eval-run-id>/<benchmark-id>/<trial-id>/trace.jsonl`
   before anything else runs, and a `run.started` event is written
   immediately.
2. **Hidden-test events.** `test.started` is emitted before hidden tests run;
   `test.completed` or `test.failed` immediately after, depending on outcome.
3. **Evidence capture**, regardless of pass/fail:
   - the `story.json` used for the run,
   - `workflow.stdout.log` / `workflow.stderr.log` / `workflow_result.json`
     (written even when the workflow itself failed — a normalization helper,
     `_normalize_workflow_result`, extracts stdout/stderr/returncode from
     whatever the workflow invocation returned: a `subprocess.CompletedProcess`,
     a plain dict, a mock, or an arbitrary object),
   - the hidden-test JUnit XML, if hidden tests ran far enough to produce one,
   - `final.diff` — a `git diff` of the sandboxed target workspace (including
     untracked files, via `git add -A -N` first) captured after the workflow
     and before sandbox cleanup.

   Every capture step tolerates its own failure: an `OSError` while writing a
   log, or a `git diff` that can't run, appends a warning to the trial's
   `evidence.warnings` list rather than crashing the run. A trial with a
   completely failed workflow still produces a structured result — evidence
   capture is best-effort *around* the trial, never a precondition for it.
4. **Terminal trace event.** In a `finally` block, `run.completed` (workflow
   exit 0) or `run.failed` (any other outcome) is written last, and the
   trace sink is closed — even if report-building later raises.
5. **Report building.** All trials collected from a run are handed to
   `eval/live_report.py::build_eval_report`, which groups them into cases,
   computes AC/tier/suite aggregates, and returns a validated `EvalReport`.
   `write_eval_report` then publishes it atomically.

## Evidence layout

```
<reports-dir>/evidence/<eval-run-id>/<benchmark-id>/<trial-id>/
├── story.json              # the story.json actually fed to the workflow
├── workflow.stdout.log
├── workflow.stderr.log
├── workflow_result.json    # {"returncode": ...}
├── hidden_tests.xml        # JUnit XML, if hidden tests produced one
├── final.diff              # git diff of the modified sandbox, incl. untracked files
└── trace.jsonl             # one JSON event per line, run.started ... run.completed|run.failed
```

Every path segment (`eval-run-id`, `benchmark-id`, `trial-id`) is sanitized
and, if sanitizing collapses two distinct raw ids to the same string, an
8-character hash of the raw id is appended so evidence directories never
silently collide.

If `--reports-dir` isn't set (or no eval run id is active), evidence capture
is skipped entirely — `run_one` still returns a normal result dict, evidence
is just `{"dir": None, "warnings": []}`.

## Trace event lifecycle

Events are written with `telemetry.make_event` / `telemetry.JsonlEventSink`,
one per line, each carrying `event_type`, `timestamp`, `run_id`, and
`status`. A passing trial's `trace.jsonl` looks like:

```
run.started
test.started
test.completed
run.completed
```

A trial whose workflow itself failed (never reaching hidden tests) looks
like:

```
run.started
run.failed
```

The terminal event (`run.completed` vs `run.failed`) is always the *last*
line — code that wants "how did this trial end" should read the last line of
the file, not scan for a specific event type.

## Honest-missing values, not zeros

Anything the runner didn't actually measure is reported as `unknown` (status
unset) or `not_collected` (a `Metric` with that explicit status), never
coerced to `0` or `0.0`. Concretely, in the v0.2 report's `scorecard`:

- `efficiency.token_usage` / `efficiency.estimated_cost` are **always**
  `not_collected` — the runner has no cost model wired up, so these are never
  reported as `0.0` the way the old report did.
- `reliability.variance` is `not_applicable` when fewer than two trials ran
  for a case (variance is undefined for n=1), and is reported as a
  provisional `known(0.0)` otherwise — see the code comment in
  `eval/live_report.py::_build_scorecard` for why this is called out as
  provisional rather than a real variance computation.
- A trial's `duration_ms` is `not_collected` if the trial dict never reported
  `wall_seconds`, instead of defaulting to `0`.

## AC status: no inference from absence

An acceptance criterion's status across a case's trials is decided from
**positive evidence only** (`eval/live_report.py::_ac_status_across_trials`):

- If every trial that *reached* hidden tests for this AC passed it → `pass`.
- If any trial that reached hidden tests failed it → `fail`.
- If a trial's hidden tests never ran for this AC (e.g. the workflow failed
  before hidden tests started, or `AC_TEST_MAP` referenced a test that never
  executed) → that trial contributes nothing to the AC's status.
- If **no** trial ever positively evaluated the AC → `unknown`.

The rule this exists to enforce: a workflow that failed before hidden tests
ran must never make its case's ACs look like `pass` by default. They come
back `unknown`, with a `failure_reason` where available, and the case's own
status (`partial`/`failed`/`error`) tells you the trial didn't succeed.

## Case status across trials

A `BenchmarkCaseResult.status` is derived from its trial statuses:

| Trial statuses | Case status |
|---|---|
| all `passed` | `passed` |
| all in `{failed, timeout}` | `failed` |
| all `error` | `error` |
| anything mixed (including some `passed` mixed with failures) | `partial` |

`aggregate_result` (a free-form dict, not schema-validated) preserves the
distribution rather than collapsing it to a single number:
`weighted_score_mean`, `trial_pass_count`, `trial_fail_count`,
`trial_pass_rate`.

## Failure categories

Raw `error` strings are mapped to a small, fixed set of failure categories
(`eval/live_report.py::_HANDLED_FAILURE_PATTERNS`) via substring match on the
lowercased error: `timeout`, `workflow_failure`, `hidden_test_failure`. Any
error that doesn't match a known pattern maps to `unknown` — it is **never
dropped or silently reclassified**; the raw string survives in
`error_summary` regardless of which category it was bucketed into.

## Atomic publication

`write_eval_report` validates the report (`validate_report_payload`) before
writing anything — an invalid report leaves the reports directory untouched.
It then writes the timestamped report file and `latest.json` as two
temp-file-plus-`os.replace` atomic writes using **identical bytes**. If the
`latest.json` write fails after the timestamped file succeeded, the error
message says so explicitly (timestamped artifact intact, `latest.json`
stale) rather than leaving that ambiguous.

## Deterministic fixture

`scripts/generate_v02_report_fixture.py` builds a v0.2 report from a fixed,
hand-written set of trial dicts with literal `created_at`/`eval_run_id`
constants (no wall-clock, no random ids) — running it twice produces
byte-identical JSON. Its output lives at
`tests/fixtures/eval/v02_report_generated.json` and is distinct from the
hand-authored `tests/fixtures/eval/v02_report.json` (which pins the schema's
shape, not a realistic run). `tests/test_baseline_capture.py` asserts the
generator's determinism and that its output round-trips cleanly through
`eval/comparison.py::compare_reports`.
