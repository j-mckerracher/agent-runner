# WorkflowRunner Shell (Prompt 8)

## What this is

A minimal, explicit, locally-testable boundary around the existing
workflow orchestration in `run.py::main`:

```
RunSpec -> WorkflowRunner -> run.py::main -> WorkflowResult
```

This is a **shell**, not a redesign. It does not extract stages, does
not change the runner/artifact contract, and does not migrate any
caller (CLI, eval, server) onto it. Those are separate, later prompts
(see "Deferred to later prompts" below).

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

## Compatibility

Nothing about the existing CLI (`run.py`'s `__main__` block), the eval
harness (`eval/runner.py`), the server, workflow ordering, prompts, or
loop/retry limits changes in this prompt. `run.py`, `core/*`,
`eval/*`, `server/*`, and `telemetry/*` are untouched. No existing
caller is migrated onto `WorkflowRunner` — the only proof of the shell
working is test-only, via the injectable adapter.

## Deferred to later prompts

* **Stage contracts** (typed per-stage inputs/outputs, `StageResult`) —
  Prompt 9. See `docs/workflow-stages.md`.
* **CLI / eval / server migration** onto `WorkflowRunner` — Prompt 10.
* **Runner-backend and artifact-contract redesign** — v0.4.
