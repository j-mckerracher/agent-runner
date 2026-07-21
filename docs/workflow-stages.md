# Explicit Workflow Stage Contracts (Prompt 9)

## What this is

A typed execution boundary for a *single* workflow stage, layered on top
of the Prompt 8 shell:

```
RunSpec -> WorkflowRunner -> run.py::main -> WorkflowResult   (Prompt 8)
WorkflowStage -> CallableStage.run -> StageResult             (Prompt 9)
```

This wraps existing stage callables — it does not extract, rewrite, or
reorder any stage's internals, and it does not migrate any production
call site in `run.py` onto it. Prompt 10 wired the CLI/eval/server
*entry paths* onto the Prompt 8 `WorkflowRunner` shell above — it did
not touch stage internals or wire any `_Stage` call site onto
`CallableStage`. That migration is still deferred (see below).

## The contract (`workflow/stages.py`)

### `WorkflowStage` (`typing.Protocol`, `@runtime_checkable`)

```python
class WorkflowStage(Protocol):
    name: str
    def run(self, context: RunContext, /, *args, **kwargs) -> StageResult: ...
```

A stable `name` plus one execution method that returns a `StageResult`.
Structural typing — nothing needs to subclass it.

### `StageResult`

```
stage_name, status, started_at, finished_at, duration_ms (property),
output, failure, span_id, parent_span_id
```

`status` is a `StageStatus` (`SUCCEEDED` / `FAILED`) — deliberately
distinct vocabulary from `telemetry.EventStatus` (`ok`/`error`/`skipped`,
which describes a telemetry *event*) and from `run.py`'s legacy
`STATUS_OK`/`STATUS_ERROR` rendering constants.

Invariants, enforced in `__post_init__` (raise `ValueError` otherwise):

* `started_at`/`finished_at` must be timezone-aware; `finished_at` may
  not precede `started_at`.
* `SUCCEEDED` ⇒ `failure is None`.
* `FAILED` ⇒ `failure is not None` **and** `output is None` — a failed
  result can never fabricate output, and can never claim failure without
  a structured `StageFailure`.

`to_dict()` is deterministic: enum via `.value`, datetimes via
`.isoformat()`, `failure` via its own `to_dict()` or `None`, unset
optionals (`output`, `failure`, `span_id`, `parent_span_id`) stay `None`
rather than being fabricated or omitted.

### `StageFailure`

```
error_type, message, stage, propagated
```

Mirrors `workflow.models.FailureDetail`'s shape.

## `CallableStage`: the delegating adapter

```python
from workflow import CallableStage

stage = CallableStage("intake", existing_intake_function)
result = stage.run(context, *args, **kwargs)
```

Wraps an existing callable exactly as-is — no signature change, no
behavior change. Each call gets a fresh timing boundary (`clock`,
injectable for deterministic tests) and invokes the wrapped callable
**exactly once**.

### `run()` vs `run_capturing()`

Mirrors `WorkflowRunner`'s own propagation doctrine (Prompt 8):

* **`run(context, *args, **kwargs)`** — propagation is the default. Any
  exception the wrapped callable raises propagates unchanged, after a
  terminal `stage.failed` event is emitted.
* **`run_capturing(context, *args, **kwargs)`** — the explicit opt-in.
  Converts only `Exception` subclasses into a `FAILED` `StageResult`.
  `SystemExit`, `KeyboardInterrupt`, and any other non-`Exception`
  `BaseException` are **never** captured — they still propagate
  unchanged. Every started stage gets exactly one terminal event
  regardless of exception type, so there is never a dangling
  `stage.started` with no matching `stage.failed`/`stage.completed`.

### `current_stage` save/restore

`context.current_stage` is set to the stage's `name` for the duration of
the call and restored to its previous value in every path — success,
captured failure, and propagated `BaseException` — via a `try/finally`
around the wrapped call. This mirrors `run.py::_Stage`'s own
`AGENT_RUNNER_CURRENT_STAGE` save/restore discipline, applied to
`RunContext.current_stage` instead of an environment variable.

### `parent_span_id` is explicit, never inferred

`parent_span_id` is only ever the value a caller passes to the
`CallableStage` constructor. It defaults to `None` and is **never**
derived from `context.trace_reference` — that field is an
evidence/reference locator (e.g. a path to a trace file), not a span id,
and treating it as one would fabricate an invalid trace relationship.
Correlating stage spans to a parent span is left to whatever wires up
`CallableStage` explicitly; nothing in this module guesses at it.

## Lifecycle events (canonical Prompt-4 trace contract)

Every `run`/`run_capturing` call emits through `telemetry.make_event`
and a caller-supplied `EventSink`:

| Call outcome | Event | `EventStatus` |
|---|---|---|
| stage begins | `EventType.STAGE_STARTED` (`"stage.started"`) | *(none — not yet terminal)* |
| callable returns | `EventType.STAGE_COMPLETED` (`"stage.completed"`) | `OK` |
| callable raises (any `BaseException`) | `EventType.STAGE_FAILED` (`"stage.failed"`) | `ERROR` |

`stage.started` is never given `EventStatus.OK` — assigning a status to
a non-terminal event would misleadingly imply the outcome is already
known. Every event carries `run_id`, `stage` (the stage name), its own
`span_id` (from an injectable `span_id_factory`, default
`telemetry.new_span_id`), and `parent_span_id` (as above).

### Sink ownership

The `sink` is supplied by the caller at `CallableStage` construction
time and is used only for `emit()`. `CallableStage` **never** calls
`sink.close()` — sink lifecycle stays entirely with whoever constructed
it. If `sink` is `None`, emission is a no-op; execution still proceeds
normally.

### Sink-failure precedence

If the sink's `emit()` itself raises while recording `stage.failed`
(e.g. the sink is unavailable), that sink exception is swallowed
locally — it never replaces or masks the original callable exception,
which remains what propagates (via `run()`) or gets captured (via
`run_capturing()`). The stage's job is to report the callable's
outcome, not to let telemetry plumbing determine it.

## Integration with the legacy stage system — deliberately not done here

`run.py` already has its own, independent stage-boundary mechanism:
`_Stage`, a context manager that emits `"stage.start"`/`"stage.end"`
(different event names entirely) via `server.events.emit`, which is a
no-op unless `AGENT_RUNNER_EVENT_LOG` is set. It manages the
`AGENT_RUNNER_CURRENT_STAGE` environment variable, not
`RunContext.current_stage`.

This prompt does not touch any production `_Stage` call site, and does
not wrap `run.py`'s real stage functions in `CallableStage` anywhere
that executes in production. Two consequences:

* **No double-emission is possible**, because the two systems use
  different event names and different sinks, and neither system's
  production call sites were changed.
* The proof that `CallableStage` itself works correctly
  (`tests/test_workflow_stages.py`'s integration-fixture test) wraps a
  *representative* legacy-style callable under a real stage-name string
  (e.g. `"intake"`) directly with `CallableStage`, with no `_Stage`
  involved. That proves the adapter emits one clean canonical
  start/terminal pair for a stage-shaped callable — it does **not**
  prove anything about nesting `CallableStage` inside a live `_Stage`
  block, since that combination is never exercised by this prompt.

Migrating a real `run.py` stage call site onto `CallableStage` — and
deciding what happens to `_Stage` at that point — remains unscheduled.
Prompt 10 did not do this; see `docs/workflow-runner.md`'s "Entry
points (Prompt 10)" section for what it did do (CLI/eval/server entry
routing onto `WorkflowRunner`, not stage internals).

## What this prompt does *not* do

* No change to `run.py`'s stage order, prompts, retry policy, or any
  production call site.
* No `WorkflowResult.stage_results` field — nothing in this prompt
  honestly populates a list of per-stage results through
  `run.py::main`'s current call path. Adding the field first and
  populating it later would be exactly the kind of ahead-of-integration
  scaffolding this refactor avoids.
* No new `AgentInvocation`/`AgentResult`/`RunnerBackend` types.
* No CLI, eval, or server migration onto stage contracts. (Prompt 10
  later routed the CLI/eval/server *entry paths* onto `WorkflowRunner`
  — a different boundary than this one — without touching stage
  contracts; see `docs/workflow-runner.md`.)

## Minimal local example

```python
from workflow import RunContext, RunSpec, CallableStage

def existing_stage_function(story_ref: str) -> dict:
    return {"intake_source": story_ref}

stage = CallableStage("intake", existing_stage_function)
context = RunContext.for_spec(RunSpec())
result = stage.run(context, "story-123")

assert result.status.value == "succeeded"
assert result.output == {"intake_source": "story-123"}
```

## Stage artifact contracts (Prompt 22)

Each canonical stage's typed artifact inputs/outputs and their cardinality are
now declared in `workflow/stage_artifacts.py` (`STAGE_ARTIFACT_REGISTRY`), with
a telemetry-emitting validation boundary in `workflow/artifact_lifecycle.py`.
These are reusable, fixture-proven contracts — not yet wired into production
orchestration (deferred to Prompt 23). See
[`docs/stage-artifact-contracts.md`](stage-artifact-contracts.md).

## Deferred to later prompts

* Migrating real `run.py` stage call sites onto `CallableStage` and
  reconciling with `_Stage` — still unscheduled (not part of Prompt 10).
* `WorkflowResult.stage_results` aggregation across a full run.
* Wiring stage contracts into the CLI/eval/server entry paths that
  Prompt 10 routed onto `WorkflowRunner`.
* Runner-backend and artifact-contract redesign — v0.4.
