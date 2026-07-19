# Runner Backend Interface (Prompt 13)

## Why this exists

Prompt 12 added the stdlib-only `runners/` contracts (`AgentInvocation`,
`AgentResult`, and supporting types) but left them unused by production, with
every runner mention promising that "a later prompt will place these behind a
`RunnerBackend`." This is that prompt.

`runners/base.py` defines the runner-neutral execution seam:
`RunnerBackend.invoke(invocation: AgentInvocation) -> AgentResult`.
`runners/legacy.py` provides one adapter, `LegacyDispatchBackend`, that proves
an `AgentInvocation` can flow through the new boundary by delegating to the
existing `core.agent_cmd.run_agent_cmd(...) -> str`.

**This changes / does not change.** This is an *abstraction seam around
existing behavior*. It **does** add a typed boundary (`RunnerBackend`) and one
legacy adapter. It **does not** change `run_agent_cmd` — same parameters, same
`-> str` return, same failover, same exceptions — nor migrate any concrete
runner, add a registry, or alter production dispatch. Existing
`run_agent_cmd -> str` callers are untouched.

```
AgentInvocation ──► RunnerBackend.invoke ──► run_agent_cmd(...) -> str ──► AgentResult
    (typed)          (LegacyDispatchBackend)     (unchanged legacy)         (typed)
```

## The `invoke` contract

`RunnerBackend` is an `abc.ABC` with a single `@abstractmethod`, `invoke`.
Being an ABC (not a `Protocol`) makes the contract runtime-enforced:
`RunnerBackend()` raises `TypeError`, and a subclass that fails to implement
`invoke` cannot be instantiated.

`invoke` takes exactly one `AgentInvocation` and returns exactly one
`AgentResult`, or raises.

### Error behavior

`invoke` **always raises** when it cannot produce a trustworthy result — it
never returns a `FAILED` result to signal a *dispatch* failure. The error
hierarchy (all raise-only):

```
RunnerBackendError(RuntimeError)              # base; carries agent/runner/model
└── RunnerInvocationError                     # dispatch could not produce a trustworthy result
    └── UnsupportedInvocationError            # pre-flight: adapter cannot honor this invocation
```

`UnsupportedInvocationError` subclasses `RunnerInvocationError` so the rule
"`invoke` raises `RunnerInvocationError` (or a subclass) whenever it cannot
produce a trustworthy `AgentResult`" holds literally, whether the failure is
detected before or during dispatch. The base error stores `.agent` / `.runner`
/ `.model`; its message **never** includes prompt text, metadata, environment
values, or credentials.

For `LegacyDispatchBackend`, raising is the only honest option on dispatch
failure: `run_agent_cmd` returns a bare `str`, so there is no exit code that
could justify a returned `FAILED` result.

## Legacy field mapping (`LegacyDispatchBackend.invoke`)

| `AgentInvocation`          | `run_agent_cmd`     | Handling                                                       |
| ------------------------- | ------------------- | ------------------------------------------------------------- |
| `agent`                   | `agent=`            | always                                                         |
| `runner`                  | `runner=`           | always                                                         |
| `prompt`                  | `prompt=`           | required text (see below)                                     |
| `model`                   | `runner_model=`     | only when not `None`                                          |
| `repo` (`Path`)           | `repo=` (`str`)     | `str(invocation.repo)` when not `None` (legacy type is `str`) |
| `change_id`               | `change_id=`        | only when not `None`                                          |
| `metadata["extra_skills"]`| `extra_skills=`     | only when present AND a list/tuple of `str` → forwarded as `list`; else rejected |

The dispatcher is called **exactly once** on success, returning
`AgentResult(status=SUCCEEDED, agent=…, runner=…, model=…,
response_text=<returned str>)`. Every other field stays `None` — no fabricated
`stdout`, `exit_code`, `duration_ms`, tokens, cost, retry, failover, artifacts,
or session log. Missing telemetry is never zeroed (see
`docs/agent-invocation-contract.md`).

### `prompt` vs `prompt_ref`

The adapter uses `invocation.prompt` when present. If only `prompt_ref` is set,
`invoke` raises `UnsupportedInvocationError` — the legacy path has no ref
loader, and this prompt does not invent one (no file reading, no resolution).

### Rejected execution controls

`timeout_s`, `working_dir`, `env_overrides`, and `allowed_tools` have no
faithful legacy equivalent. If any is **present** (`is not None`) the adapter
raises `UnsupportedInvocationError` and does **not** call the dispatcher.
Rejection is on presence, not truthiness: even `env_overrides={}` or
`allowed_tools=()` is rejected, because the caller supplied the control.
Silently dropping it would mislead; forwarding it would raise `TypeError` in
`run_agent_cmd`.

### Deliberately unconsumed (safe)

`trace_context` and `run_id` are tracing/identity, not execution controls, so
they are ignored — the legacy path has its own telemetry via the
`@track_with_ui` decorator. All non-`extra_skills` metadata keys are ignored:
`metadata` is a free-form annotation bag by contract and is never forwarded as
`**kwargs`.

An `extra_skills` value that is present but not a list/tuple of `str` is a
requested-but-invalid set, so it raises `UnsupportedInvocationError` rather
than being silently dropped or forwarded as a bad value.

## How `run_agent_cmd -> str` compatibility is preserved

`core.agent_cmd` is **not** imported at module scope in `runners/legacy.py`.
The default dispatcher is imported lazily inside `invoke`, so
`from runners.legacy import LegacyDispatchBackend` loads no `core`; the legacy
stack is pulled in only on the first default-path call. Tests inject a fake
dispatcher via the constructor (`LegacyDispatchBackend(dispatcher=fake)`),
exercising the adapter with no `core` import at all. `run_agent_cmd` keeps its
exact signature and behavior — the seam wraps it, it does not replace it.

`LegacyDispatchBackend` is intentionally **not** re-exported from the `runners`
package root and is not in `runners.__all__`; its public path is
`from runners.legacy import LegacyDispatchBackend`. This keeps
`from runners import *` (and a bare `import runners`) from pulling in the legacy
execution stack. The interface and error classes in `runners.base` are
stdlib-only and are re-exported from the root.

## Non-goals / deferred work

Out of scope for this prompt: concrete backend migrations
(Claude/Codex/Gemini/Copilot/OMP/openai-compat), a runner registry, backend
selection or config loading, any CLI/workflow/eval/server changes, changes to
`run_agent_cmd`'s params/return/failover/exceptions, moving failover into the
backend, retry, lifecycle events, hashing, `ArtifactRef`, prompt-ref file
resolution, and async/stream/batch execution.

Update (Prompt 14): concrete per-family adapters now exist alongside the legacy
adapter — see [`docs/runner-backend-adapters.md`](runner-backend-adapters.md).
`run_agent_cmd` and `LegacyDispatchBackend` remain unchanged.
