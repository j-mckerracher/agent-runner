# Runner Failover

`runners.failover` (Prompt 16) is the **policy boundary** for the runner backend
layer: it composes

```
AgentInvocation -> FailoverExecutor -> RunnerRegistry -> RunnerBackend.invoke -> AgentResult
```

executing an explicit ordered route of backend attempts and recording ordered
`FailoverMetadata` hops.

Failover policy lives *here* — not inside workflow stages and not inside
individual adapters. This establishes the seam only. It does **not** migrate any
production call site: the live `core.run_cmds.run_agent_cmd` loop and
`core.runner_failover.RunnerFailoverPolicy` remain untouched. Production dispatch
migration is **explicitly deferred** to a later prompt.

## Public API

```python
from runners import (
    RunnerRoute,
    FailoverPlan,
    FailoverExecutor,
    default_failover_eligibility,
    FailoverConfigurationError,
    FailoverMetadataConflictError,
    FailoverExhaustedError,
)
```

- `RunnerRoute(runner, model=None)` — one ordered attempt. `frozen`. Validates a
  nonempty non-blank `runner` and a nonempty-or-`None` `model`.
- `FailoverPlan(entries)` — an ordered, validated, duplicate-free route.
  `entries` is an iterable of `RunnerRoute` or route mappings
  (`{"runner": ..., "model"?: ...}`), coerced to `RunnerRoute`.
- `FailoverExecutor(registry=None, *, eligibility=None)` —
  `execute(invocation, plan) -> AgentResult`. The default `registry` builds no
  backends (its factories are lazy); the default `eligibility` is
  `default_failover_eligibility`.
- `default_failover_eligibility(*, invocation, result, error, attempt_index)` —
  the conservative default predicate.

The `FailoverEligibility` typing `Protocol` is an internal aid and is **not**
exported.

## Route construction & registry interaction

A `FailoverPlan` is the *ordered* list of backends to try. `FailoverExecutor`
resolves each route through the injected `RunnerRegistry` **lazily** — a later
route is never resolved once an earlier attempt succeeds or the run stops.
Resolution failures from the registry (`UnknownRunnerError`,
`InvalidRunnerNameError`, `RegistryConfigurationError`) are all
`RunnerBackendError` subclasses and therefore travel the same eligibility path
as errors raised from `RunnerBackend.invoke`.

`FailoverPlan` rejects (never silently discards):

- an empty route,
- malformed entries (non-`RunnerRoute`, non-mapping, missing `runner`, unknown
  mapping keys),
- duplicate identical attempts.

Duplicate detection reuses `runners.registry._normalize` — the same runner-name
normalization the registry uses for selection — so failover dedup can never
drift from registry selection. The dedup key is
`(normalized_runner, route_model_as_specified)`: the same runner with two
distinct models is allowed; the same runner (case-insensitive) with the same
model is a duplicate. All violations raise `FailoverConfigurationError`.

`execute` enforces a typed-only contract: a non-`AgentInvocation` or
non-`FailoverPlan` argument raises `FailoverConfigurationError` rather than
letting an incidental `AttributeError`/`TypeError` escape.

## Invocation field preservation & the model-inheritance rule

Each attempt derives a **fresh** `AgentInvocation` from the original, changing
only `runner` (always) and `model` (only when the route names one). Every other
field is preserved, and the original invocation is never mutated; construction
re-copies `env_overrides`/`metadata` so nested values are not shared.

**Model-inheritance rule:** a route entry with `model=None` inherits the
*original invocation* model — **not** the model selected by the previous route
entry. Each attempt's model is therefore `route.model or invocation.model`,
always relative to the original.

## Default eligibility

`default_failover_eligibility` is deliberately conservative:

- continue on any `RunnerBackendError`;
- continue on an `AgentResult` whose status is `FAILED` or `TIMED_OUT`;
- otherwise `False`.

There is **no** provider-specific quota-string parsing here — that stays in
`core.runner_failover`. Callers wanting quota-aware eligibility inject a custom
predicate. The predicate never sees `KeyboardInterrupt`/`SystemExit` (the
executor re-raises those first) and never sees arbitrary programmer errors (the
executor only catches `RunnerBackendError`).

## Failure & exhaustion behavior

Per attempt (index `i`, route `r`):

1. Derive the fresh invocation, resolve, invoke.
2. **Success** (`status == SUCCEEDED`): stop immediately, attach accumulated
   hops, return.
3. **Unsuccessful** (returned `FAILED`/`TIMED_OUT`, or a caught
   `RunnerBackendError`):
   - **not eligible** → non-failover path: a returned result is returned (with
     any hops so far); a raised error is re-raised unchanged. Never mislabeled
     as exhaustion.
   - **eligible and last route** → raise `FailoverExhaustedError`.
   - **eligible and a later route exists** → record one hop, continue.

`KeyboardInterrupt`/`SystemExit` propagate untouched. Any non-`RunnerBackendError`
exception propagates unwrapped — programmer errors are never swallowed.

## `FailoverMetadata`: transition-only semantics

`failover_attempts` records **transitions only** — one `FailoverMetadata` per hop
from a failed route to the next:

- `attempt_index = i` (zero-based index of the failing *source* attempt),
- `from_runner = r.runner`, `from_model = r.model or invocation.model`,
- `to_runner = entries[i+1].runner`, `to_model = entries[i+1].model or invocation.model`,
- `reason`: a leak-free typed token — `type(error).__name__` for a raised error,
  or `status.value` (plus `":" + error_type` when present) for a returned
  failure. Free-form `error_message`/`stderr`/`response_text` are **excluded**.

The **final** attempted runner/model (the terminal failure with no destination)
is **not** a `FailoverMetadata` record — it is carried separately on
`FailoverExhaustedError`. Therefore:

> **total attempts = `len(failover_attempts) + 1`** on exhaustion.

For N failed routes there are N−1 hop records. Tests must not expect one metadata
record per backend invocation.

## `final_result` sensitivity & redaction

`FailoverExhaustedError` carries:

- `.failover_attempts` — the ordered tuple of transitions,
- `.final_result: AgentResult | None` — the last failed result, or `None` when
  the last attempt raised (in which case the original exception is chained via
  `__cause__`),
- `.agent`/`.runner`/`.model` — the identity of the final attempt (via the
  `RunnerBackendError` base).

The exception **message** is a safe summary — attempt count plus the final runner
name only. It never includes prompt, environment, credential, or free-form text.

`final_result` may still hold potentially sensitive diagnostic data
(`response_text`, `stdout`, `stderr`, `error_message`, `metadata`). It is
deliberately **excluded** from the message and from the default
`__str__`/`__repr__` rendering. Callers that surface `final_result` must render
it through their own redacted summary.

## Metadata composition conflict

`_attach_failover_metadata` composes the recorded hops onto the returned result:

- no hops → the result is returned unchanged, preserving any backend-provided
  `failover_attempts` (no record is added when the primary succeeds without
  failover);
- hops present and `result.failover_attempts is None` → the hops are attached
  (all other result fields preserved);
- hops present and the backend already returned its own `failover_attempts` →
  ambiguous composition → `FailoverMetadataConflictError` (never a silent
  overwrite).

## Error taxonomy

| Error | Base | When |
| ----- | ---- | ---- |
| `FailoverConfigurationError` | `ValueError` | build/argument-time faults: invalid route/plan, invalid `execute` argument types |
| `FailoverMetadataConflictError` | `RunnerBackendError` | execution-time: backend returned its own `failover_attempts` while the runner layer also recorded hops |
| `FailoverExhaustedError` | `RunnerBackendError` | every eligible route was attempted and none succeeded |

`FailoverConfigurationError(ValueError)` mirrors `AgentContractError(ValueError)`
style — bad configuration is a `ValueError`, not a raw
`RuntimeError`/`KeyError`/`TypeError`. The two execution-time errors are
`RunnerBackendError` subclasses, so they carry the safe
`agent`/`runner`/`model` identity and never a prompt/metadata/env/credential
payload.

## Dependency & isolation guarantees

`runners.failover` is a leaf module: it imports only stdlib
(`dataclasses`/`typing`) plus sibling `runners.base`/`runners.models`/
`runners.registry`. Importing it or constructing a `FailoverExecutor` loads
nothing from `core`, `workflow`, `server`, `eval`, `telemetry`, `opik`, or any
vendor SDK — the default registry builds no backends (lazy factories) and the
adapters lazy-import their `core.run_cmds` callable inside `invoke`. Subprocess
isolation probes assert the forbidden set, extended for Prompt 16 with the two
Google GenAI SDK paths (`google.generativeai`, `google.genai`).

## Relationship to legacy `core.runner_failover`

The live failover path — `core.runner_failover.RunnerFailoverPolicy` plus the
`run_agent_cmd` loop in `core.run_cmds` — is unchanged and still owns
provider-specific quota-string parsing and job-history candidate discovery. This
seam is the structured, leaf-safe replacement target: eligibility is decided from
typed `AgentResult`/`RunnerBackendError` values via an injected predicate, and
quota parsing is composed in by callers rather than baked into the seam.

## Deferred: production migration

No workflow, CLI, eval, or server call site is migrated. Replacing `run_agent_cmd`
dispatch, wiring quota-aware eligibility, and job-history candidate discovery are
out of scope for Prompt 16 and belong to a later prompt.
