# Runner Lifecycle Events & Safe Hash Capture

`InstrumentedRunnerBackend` is the reusable **runner-boundary instrumentation
seam**. It wraps a single `RunnerBackend`, emits canonical Trace Contract v1
lifecycle events around one `invoke` call, captures safe SHA-256 prompt/response
hashes plus result telemetry, and returns the wrapped backend's `AgentResult`
unchanged (or re-raises its exception unchanged). Instrumentation never changes
the backend's outcome.

The design mirrors `workflow.stages.CallableStage`: an injected clock + span-id
factory, one start event, exactly one terminal event, sink-failure swallowing so
the backend outcome always wins, and `BaseException` handling that still
re-raises.

## Public import path

```python
from runners.lifecycle import InstrumentedRunnerBackend
```

The module is intentionally **not** re-exported from `runners/__init__.py`, so
`import runners` stays a `telemetry`-free leaf. Import the seam by its full path
when you want instrumentation.

## Event ↔ status mapping

Every instrumented, sink-enabled invocation emits exactly two events: one
`agent.invocation.started`, then one terminal event.

| Outcome | Terminal event | `status` |
|---|---|---|
| returned `AgentResultStatus.SUCCEEDED` | `agent.invocation.completed` | `ok` |
| returned `AgentResultStatus.FAILED` | `agent.invocation.failed` | `error` |
| returned `AgentResultStatus.TIMED_OUT` | `agent.invocation.timed_out` | `error` |
| raised exception (any `BaseException`) | `agent.invocation.failed` | `error` |

`agent.invocation.timed_out` is a distinct family (added additively, schema
stays v1) so a timeout is never conflated with a generic failure.

## Run / span / parent-span rules

- `run_id` comes from `invocation.run_id`. In sink mode it MUST be a nonempty
  string; otherwise `RunnerLifecycleConfigurationError` is raised **before** the
  backend is called.
- `span_id` = `invocation.trace_context.span_id` when present, else one freshly
  generated via the injected `span_id_factory` (default `telemetry.new_span_id`).
- `parent_span_id` = `invocation.trace_context.parent_span_id` (or absent).
- The same `span_id` / `parent_span_id` appear on **both** the start and the
  terminal event.

## Hash algorithm & exact source fields

- Algorithm: SHA-256 hex digest of the UTF-8 encoding of the source text.
- `prompt_sha256` = hash of `invocation.prompt`. It is emitted on **both** the
  start and terminal events. `None` prompt → field absent. The wrapper never
  reads `invocation.prompt_ref` and never touches the filesystem.
- `response_sha256` = hash of `result.response_text`. **Terminal event only.**
  `None` response → field absent. It never substitutes `stdout`, `stderr`, or
  any error text.

## Missing-vs-zero metric behavior

Metrics (`duration_ms`, `tokens_in`, `tokens_out`, `cost_usd`) are copied
exactly from the returned result:

- `None` means "not measured" and is omitted from the serialized event.
- An observed `0` (or `0.0`) is a real measurement and is preserved.
- `duration_ms`: `result.duration_ms` wins when not `None` (including an observed
  `0`). Otherwise the wrapper falls back to the measured wall-clock elapsed
  (`finished_at - started_at`).

## Backend-outcome precedence (all post-execution instrumentation)

The wrapped `backend.invoke` call is made exactly once and is never wrapped in a
best-effort guard. **Everything after it** — clock reads, field preparation, and
event emission — is best-effort:

- On a returned result, if terminal instrumentation raises, the exact original
  result object is still returned (unmutated, same identity).
- On a raised exception, terminal instrumentation is best-effort and the bare
  `raise` re-raises the **original** backend exception object unchanged.
- A failing sink (`emit` raises) can never change the outcome — sink/
  serialization failures are swallowed.

This precedence covers all post-execution failures (clock, field prep, emission),
not just sink errors.

## Raw-content safety guarantee (scope-limited)

The wrapper guarantees that **raw prompt/response text never enters any artifact
it creates**: no `TraceEvent`, no event `metadata`, no wrapper-created
`RunnerLifecycleConfigurationError`, and no other message the wrapper constructs.
Only SHA-256 digests of that text ever reach telemetry.

Exception-message handling:

- A raised `RunnerBackendError` message is contract-safe (identity only) and MAY
  be emitted as `error_message` — but only when nonempty.
- A raised unexpected `Exception` / `KeyboardInterrupt` / `SystemExit` emits the
  exception **type name only**; its message is omitted.

The wrapper does **not** sanitize arbitrary backend exceptions. A bare `raise`
re-raises the exact backend exception object unchanged, so if a backend embeds
sensitive text in its own exception message the wrapper neither modifies nor
suppresses that object. The two are reconciled by never *copying* raw content
into wrapper-created artifacts while still preserving the backend's exception
identity.

## Deferred: safe capture of returned `error_message`

For a **returned** `FAILED` / `TIMED_OUT` result, the wrapper emits `error_type`
only when it is a nonempty string. It deliberately does **not** copy
`result.error_message` — that field is untrusted free-form text with no contract
that it excludes prompt/response/credential/provider content. Safe capture of a
returned `error_message` is deferred until a separate sanitization / trusted-error
contract exists.

## Sink ownership

The `EventSink` is caller-owned. The wrapper never constructs, flushes, or closes
it — sink lifecycle (including durability via `flush`/`close`) stays entirely with
the caller.

## No-sink transparent mode

Constructed without a sink, `invoke` is pure delegation: no trace, no `run_id`
requirement, the exact result returned, the exact exception re-raised.

## Import-isolation boundary

`runners/lifecycle.py` imports only stdlib, `runners.*`, and the local
`telemetry` package. It imports none of `core`, `workflow`, `server`, `eval`,
`opik`, or any vendor SDK, and starts no subprocess. `telemetry` **is** an
allowed dependency of `runners.lifecycle`, but NOT of `runners/__init__.py`.

## Usage example (fake backend + JSONL)

```python
from runners.base import RunnerBackend
from runners.lifecycle import InstrumentedRunnerBackend
from runners.models import AgentInvocation, AgentResult, AgentResultStatus
from telemetry.event_sink import JsonlEventSink


class MyBackend(RunnerBackend):
    def invoke(self, invocation):
        return AgentResult(status=AgentResultStatus.SUCCEEDED, response_text="hi")


with JsonlEventSink("trace.jsonl") as sink:
    wrapper = InstrumentedRunnerBackend(MyBackend(), sink=sink)
    result = wrapper.invoke(
        AgentInvocation(agent="planner", runner="claude", prompt="do it", run_id="run-1")
    )
# trace.jsonl now holds two lines:
#   agent.invocation.started   (prompt_sha256 present)
#   agent.invocation.completed (prompt_sha256 + response_sha256 present)
```

## Scope note

This prompt builds the seam and its deterministic JSONL proof only. Live
production dispatch migration — `core/`, `run.py`, CLI, eval, server, workflow,
registry, and failover call sites — is intentionally **deferred**.
