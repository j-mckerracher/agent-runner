# WorkflowRunner Shell (Prompt 8)

## What this is

A minimal, explicit, locally-testable boundary around the existing
workflow orchestration in `run.py::main`:

```
RunSpec -> WorkflowRunner -> run.py::main -> WorkflowResult
```

This is a **shell**, not a redesign. It does not extract stages and
does not change the runner/artifact contract. Prompt 10 (below) wires
the CLI entry path onto this boundary; eval and server reach it
through that same CLI path, unchanged. See "Entry points (Prompt 10)".

## The models (`workflow/models.py`)

### `RunSpec`

Frozen (immutable) dataclass describing a single requested run. Every
field maps directly to a parameter `run.py::main` already accepts —
nothing here is speculative:

```
repo_path, change_id, ado_url, story_path, manual_story_path,
runner, model, agent_llm_overrides, extra_context,
skip_lessons_optimizer, skip_materialize, calibration_fast_mode,
headless, log_level
```

`repo_path`/`story_path`/`manual_story_path` accept either a `str` or a
`Path`; strings are coerced to `Path` in `__post_init__`.

**Validation** mirrors `core.workflow_inputs.resolve_workflow_input`
exactly: providing more than one of `ado_url`, `story_path`,
`manual_story_path` raises `ValueError`. Providing **none** of them is
*not* an error — the legacy seam silently defaults to a synthetic story
fixture in that case, and `RunSpec` does not diverge from that
behavior by inventing a stricter "at least one required" rule.

### `RunContext`

Mutable, per-run state created fresh by `RunContext.for_spec(spec)` for
every `WorkflowRunner.run`/`run_capturing` call. It is never a global
and never shared between runs:

```
run_id, spec, started_at, current_stage,
trace_reference, artifact_references, runtime_metadata
```

`trace_reference` and `artifact_references` stay `None`/`()` in this
prompt — the shell does not construct, own, or resolve either. `spec`
is the original `RunSpec`; the context does not duplicate any path
resolution `run.py::main` performs internally (it does not claim to
have a "resolved" story or repo path, because it doesn't compute one).

### `WorkflowResult`

```
run_id, status, started_at, finished_at, duration_ms (property),
final_output, failure, trace_reference, artifact_references,
runtime_metadata
```

`status` is a `RunStatus` (`SUCCEEDED` / `FAILED`), matching
`run.py`'s own `STATUS_SUCCEEDED`/`STATUS_FAILED` strings — not
telemetry event-name vocabulary. There are no token/cost/metric
fields: fields that are not currently meaningful are simply absent
rather than present-but-fabricated as `None` placeholders.

### `FailureDetail`

Attached to a `WorkflowResult` only when `run_capturing()` converts an
exception into a failed result: `error_type`, `message`, `stage`,
`propagated`.

## The runner (`workflow/runner.py`)

```python
from workflow import RunSpec, WorkflowRunner

runner = WorkflowRunner()          # default adapter -> run.py::main
result = runner.run(spec)          # propagates exceptions, like run.py::main does
```

`WorkflowRunner` takes an optional `legacy_workflow` callable
(`(spec, context) -> Any`) — the default is `_default_legacy_workflow`,
which lazily `import run`s and calls `run.main(**kwargs)` with the
spec's fields translated to `run.main`'s actual parameter names
(`story_path` -> `story_file`, `manual_story_path` ->
`manual_story_file`, etc.). Injecting a fake callable is how tests
exercise the shell without ever importing `run.py` or running a real
workflow.

### Two ways to call it

* **`runner.run(spec)`** — the default. Calls the adapter exactly once.
  On success, returns a `SUCCEEDED` `WorkflowResult` wrapping the
  adapter's return value as `final_output`. On failure, **the
  exception propagates unchanged** — this matches `run.py::main`'s own
  contract, which re-raises on failure and never swallows
  `SystemExit`. Propagation is the default, not an opt-in.
* **`runner.run_capturing(spec)`** — the explicit, separate opt-in for
  callers that want a structured `FAILED` result instead of a raised
  exception. It only catches `Exception`; `BaseException` subclasses
  that are not `Exception` (`SystemExit`, `KeyboardInterrupt`) still
  propagate.

### What the shell does *not* do

* It does not emit any new lifecycle telemetry (`run.started` /
  `run.completed` / `run.failed`, etc.). `run.py::main` already owns
  its own trace emission via `telemetry`; this shell does not
  duplicate or compete with it.
* It does not construct or close a trace sink. Any sink usage remains
  entirely inside `run.py::main`, unaffected by this prompt.
* It does not import `run`, `server`, or `opik` at package-import
  time — `import run` happens lazily, inside `_default_legacy_workflow`,
  only when the default adapter is actually invoked. Constructing a
  `WorkflowRunner` with an injected fake callable never touches `run.py`
  at all (see `tests/test_workflow_isolation.py`).
* It does not extract or reimplement any stage/ordering logic from
  `run.py::main`. The adapter is a pure argument-translation layer.

## Minimal local example

```python
from workflow import RunSpec, WorkflowRunner

def fake_workflow(spec, context):
    return f"ran for {spec.change_id}"

runner = WorkflowRunner(legacy_workflow=fake_workflow)
result = runner.run(RunSpec(change_id="12345"))

assert result.status.value == "succeeded"
assert result.final_output == "ran for 12345"
```

## Entry points (Prompt 10)

Prompt 9 left `WorkflowRunner` wired but unused by any real caller.
Prompt 10 routes the CLI, eval, and server entry paths through it,
without rewriting any of them:

```
CLI            run.py __main__ -> execute_cli_args -> RunSpec -> WorkflowRunner -> run.main
Eval           eval/runner.py subprocess -> `python run.py ...` -> (same CLI path above)
Server (job)   server/runner_proc.py subprocess -> `python run.py ...` -> (same CLI path above)
Server (bench) server/runner_proc.py subprocess -> `python eval/runner.py ...` -> (Eval path above)
```

Eval and server already invoked `run.py` as a subprocess with CLI
flags — neither one ever re-implemented `run.py::main`'s internal
argument mapping. So the only place that bypassed `WorkflowRunner` was
`run.py`'s own `__main__` block, which called `main(...)` directly.
Fixing that single seam is enough to bring all three entry paths behind
the shared boundary; **no code in `eval/runner.py` or
`server/runner_proc.py` changed for Prompt 10.**

### Two distinct mapping boundaries — do not confuse them

```
run.py::run_spec_from_args                  parsed CLI arguments -> RunSpec
workflow.runner::_default_legacy_workflow   RunSpec -> run.main(**kwargs)
```

`run_spec_from_args` is the **authoritative, single, tested**
conversion from `argparse.Namespace` to `RunSpec` — every CLI flag is
mapped explicitly (including cases where the CLI's argparse default
differs from `RunSpec`'s own default, e.g. `--skip-lessons-optimizer`
defaults to `False` via `store_true` while `RunSpec.skip_lessons_optimizer`
defaults to `True`; the mapping always passes the CLI's actual value
through rather than relying on either default). It does not know
about `run.main`'s parameter names.

`_default_legacy_workflow` (pre-existing, Prompt 8) is the separate,
already-tested conversion from `RunSpec` to `run.main`'s actual keyword
arguments (`story_path` -> `story_file`, `Path` -> `str`, etc.). It
does not know about argparse.

Neither function duplicates the other's job.

### `run.py`'s CLI adapter functions

```python
def run_spec_from_args(args: argparse.Namespace) -> RunSpec: ...
def execute_cli_args(args: argparse.Namespace) -> WorkflowResult: ...

if __name__ == "__main__":
    args = parse_args()
    try:
        execute_cli_args(args)
    except INPUT_VALIDATION_ERRORS:
        sys.exit(1)
```

* `execute_cli_args` constructs exactly one `RunSpec` and calls
  `WorkflowRunner().run(...)` exactly once, using `run()` (not
  `run_capturing()`) so exceptions propagate unchanged — matching
  `main`'s pre-existing re-raise contract. `SystemExit` (including the
  `SIGTERM` handler's `sys.exit(143)`) is never caught here either.
* `INPUT_VALIDATION_ERRORS = (FileNotFoundError, ValueError)` catches
  both `RunSpec.__post_init__`'s story-mutual-exclusivity `ValueError`
  and any `FileNotFoundError`/`ValueError` raised deeper inside
  `run.main` — both still map to CLI exit code `1`, exactly as before.
  Any other exception type is **not** remapped and propagates with its
  original traceback, same as before this prompt.
* **No recursion is possible.** `_default_legacy_workflow` calls
  `run.main` directly — never `execute_cli_args` — so the only call
  graph is `execute_cli_args -> WorkflowRunner.run ->
  _default_legacy_workflow -> run.main`, a straight line with `main` as
  the leaf. `main`'s body, `main.fn = main`, and programmatic callers of
  `run.main(...)` are all unchanged.

### Why eval/server keep their subprocess boundary

Eval and server retain subprocess isolation deliberately — timeouts,
process-group cancellation (`os.killpg`), event-log tailing, progress
polling, and evidence capture all depend on the workflow running in a
separate OS process, not in the calling Python process. Prompt 10 does
not convert either into an in-process `WorkflowRunner` call; it only
ensures the subprocess they already launch (`run.py`) reaches
`WorkflowRunner` internally once it starts.

## Compatibility

Workflow ordering, prompts, loop/retry limits, and stage internals are
unchanged. `core/*`, `eval/*` (behavior), `server/*` (behavior), and
`telemetry/*` are untouched. `run.py::main`'s body, signature, and
`main.fn` compatibility attribute are unchanged — only `__main__` was
rewired to reach it through `WorkflowRunner` instead of calling it
directly.

## Deferred to later prompts

* **Stage contracts wired into production call sites** — still
  deferred; see `docs/workflow-stages.md`.
* **Runner-backend and artifact-contract redesign** — v0.4.
