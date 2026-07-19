# Agent Invocation & Result Contract (v0.4)

## Why this exists

Agent execution today is untyped. `core.agent_cmd.run_agent_cmd(runner,
prompt, agent, **kwargs) -> str` returns raw text, and everything else about
*one agent execution* — telemetry, identity, failover — is scattered across
ad-hoc dicts in `core/run_cmds.py` (`_emit_llm_call_event`,
`_write_cli_session_log`) plus the frozen `RunnerFailoverCandidate`
dataclass. There is **no runner-neutral object** describing one agent
execution and its result.

`runners/models.py` is that object: typed, stdlib-only data contracts —
`AgentInvocation` (the requested execution) and `AgentResult` (its outcome),
plus small supporting types (`AgentResultStatus`, `TraceContext`,
`RetryMetadata`, `FailoverMetadata`). A best-effort compatibility helper,
`runners/compat.py::from_completed_process`, maps the `CompletedProcess`
shape runners already produce into an `AgentResult`.

**Prompt 12 does not change production dispatch.** `run_agent_cmd` keeps its
exact `-> str` signature and behavior. These contracts are additive and
currently unused by production — code can adopt them incrementally.

## `AgentInvocation` — the requested execution

Immutable (`frozen`) description of a single requested agent execution.
Construction is pure: path fields are coerced `str -> Path` without touching
the filesystem, and no runner call, config read, or filesystem access
happens.

**Required** (validated nonempty): `agent`, `runner`. Custom/unknown runner
names stay representable — this contract is not a registry.

**Prompt source** (at least one required): `prompt`, `prompt_ref`.

**Optional**: `model` (validated nonempty *when supplied*; `None` is
accepted because the production boundary passes `runner_model=None` and the
concrete model may be resolved later in dispatch), `repo`, `working_dir`,
`timeout_s` (rejects `< 0`; `None`/`0`/positive allowed), `env_overrides`,
`allowed_tools`, `trace_context`, `change_id`, `run_id` (the sole run
identifier), `metadata`.

`env_overrides` and `metadata` are defensively copied at construction and
`allowed_tools` is normalized to a tuple, so a frozen instance is truly
immutable against later caller mutation.

## `AgentResult` — the outcome

Mutable (matching `workflow.models.WorkflowResult`) because callers assemble
it incrementally as telemetry arrives.

**Required**: `status` (`AgentResultStatus`).

**Identity** (caller-supplied, optional): `agent`, `runner`, `model`.

**Output**: `response_text`, `stdout`, `stderr` (kept separate), `exit_code`,
`session_log_ref`.

**Telemetry**: `duration_ms`, `tokens_in`, `tokens_out`, `cost_usd`.

**Error** (structured, supported but not required): `error_type`,
`error_message`.

**Failover / retry**: `retry` (`RetryMetadata`), `failover_attempts` (an
ordered tuple of `FailoverMetadata`, one per hop, preserving order and
intermediate failures).

**Artifacts**: `artifacts_touched` — minimal string paths, no `ArtifactRef`.

Nothing here parses runner output into invented metrics, and success is not
forced to have nonempty `stdout`/`response_text`.

### `AgentResultStatus`

Exactly three states, no speculative vocabulary:

- `succeeded` — the execution completed and reported success.
- `failed` — the execution completed and reported an error.
- `timed_out` — the execution was cut off by a timeout. Distinct from
  `failed` so a timeout is never confused with a runner-reported error.
  `timed_out` is never *inferred* by `from_completed_process` (a
  `CompletedProcess` cannot express a timeout) — only an explicit `status=`
  argument sets it.

## Missing vs. observed-zero metric semantics

Every telemetry field defaults to `None`, which means **missing / not
measured** and is *omitted* from `to_dict()`. An observed `0` / `0.0` is a
real measurement and is kept. This mirrors `telemetry/events.py` and exists
for the same reason: a `token_usage` of `0` and a `token_usage` that was
never measured must not look identical to a reader. "We didn't collect this"
is not "this measured as zero."

### Tri-state collection fields

`failover_attempts`, `artifacts_touched` (and `AgentInvocation`'s
`env_overrides` / `allowed_tools`) are honest about missing data with three
distinct states:

- `None` — collection was unavailable / not performed → **omitted** from the
  dict.
- `()` / `{}` — collected, and there was genuinely nothing → serialized as
  `[]` / `{}`.
- populated → serialized as the list / dict of values.

## Serialization

`AgentInvocation` and `AgentResult` (and each supporting type) support
`to_dict()` / `to_json()` / `from_dict()`:

- `None` fields are omitted; enum values serialize via `.value`; `Path`
  fields serialize as `str`; nested types serialize via their own
  `to_dict()`.
- `metadata` (and `env_overrides`) JSON-ability is guarded: a non-encodable
  value raises `MetadataSerializationError` *before* any partial output is
  produced.
- `to_json()` produces a deterministic, sorted-keys line.
- `from_dict()` round-trips deterministically and **ignores unknown
  top-level keys** for forward-compatibility — custom data belongs in
  `metadata`, not as invented attributes.

### Example

```json
{
  "status": "succeeded",
  "agent": "coder",
  "runner": "codex",
  "model": "gpt-5",
  "stdout": "patch applied",
  "exit_code": 0,
  "duration_ms": 0.0,
  "failover_attempts": [
    {"attempt_index": 0, "from_runner": "claude", "to_runner": "codex", "reason": "rate_limited"}
  ]
}
```

No secrets, no fabricated telemetry: `tokens_in`/`tokens_out`/`cost_usd` are
absent because they were not measured, while `duration_ms: 0.0` is a real
observed value that is kept. The single `failover_attempts` entry records a
real hop from `claude` to `codex`.

## Relationship to `run_agent_cmd` (why dispatch is unchanged)

`run_agent_cmd` still returns `str`. Prompt 12 introduced only the data
shapes; Prompt 13 places them behind the `RunnerBackend` abstraction (see
`docs/runner-backend-interface.md`) via `LegacyDispatchBackend`, which
delegates to `run_agent_cmd` without changing it. Keeping dispatch untouched
isolates the contract change from any behavioral change to production runners.

### Current-code mapping (realized by the `RunnerBackend` seam)

- `run_agent_cmd` inputs → `AgentInvocation`: `runner` → `runner`, `prompt` →
  `prompt`, `agent` → `agent`, kwarg `runner_model` → `model`, `repo` →
  `repo`, `change_id` → `change_id`, `extra_skills` → `metadata`.
  `timeout_s` / `working_dir` / `env_overrides` / `allowed_tools` /
  `trace_context` / `run_id` are **not** current params (they are sourced
  from the environment, derived, or hardcoded) and remain optional — the gap
  the contract formalizes.
- Runner text output + `CompletedProcess` (`returncode`/`stdout`/`stderr`,
  the monkey-set `_agent_runner_duration_ms`), the `_emit_llm_call_event`
  fields (`tokens_in`/`tokens_out`/`cost_usd`/`duration_ms`/`status`/
  `error_category`), and the ordered `RunnerFailoverPolicy._exhausted`
  entries → `AgentResult` (each `_exhausted` entry → one `FailoverMetadata`
  in `failover_attempts`, order preserved). Fields with no current source
  (`session_log_ref` while session logging is disabled, tokens the provider
  omits, uncollected artifacts) stay `None` / absent — never fabricated.

## Non-goals / deferred work

Out of scope for this prompt: a runner registry, adapting
Claude/Codex/Gemini/Copilot/omp/openai-compat, changing `run_agent_cmd`'s
return type, `ArtifactRef`, new production lifecycle events, and any
prompt/response hashing beyond existing fields. The `RunnerBackend` seam
itself arrives in Prompt 13 (`docs/runner-backend-interface.md`).
