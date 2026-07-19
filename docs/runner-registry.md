# Runner Registry

`RunnerRegistry` (Prompt 15) is the **selection boundary** for the runner
backend layer: it maps a runner name to exactly one `RunnerBackend`, replacing
scattered `if/elif` runner-name dispatch with one explicit, testable operation.

This establishes the selection boundary only. It does **not** migrate any
production call site — `run_agent_cmd`, workflow stages, CLI, server, and eval
remain untouched.

## Public API

```python
from runners import RunnerRegistry, resolve_backend
```

- `RunnerRegistry(factories=None, supports_openai_compat_alias=None)` — the
  testable core. `resolve(runner) -> RunnerBackend`.
- `resolve_backend(runner, *, supports_openai_compat_alias=None)` — stateless
  convenience wrapping a one-shot `RunnerRegistry(...).resolve(runner)`.

Structured errors are also exported: `RunnerSelectionError` (base),
`UnknownRunnerError`, `InvalidRunnerNameError`, `RegistryConfigurationError`.

## Built-in mappings

| runner name          | backend                        |
| -------------------- | ------------------------------ |
| `claude`             | `ClaudeBackend`                |
| `codex`              | `CodexBackend`                 |
| `gemini`             | `GeminiBackend`                |
| `copilot`            | `CopilotBackend`               |
| `copilot-<name>`     | `CopilotBackend` (prefix family) |
| `openai-compat`      | `BuiltinOpenAICompatBackend`   |

The copilot family is prefix-only, faithful to
`core.runner_models.is_copilot_runner`: `copilot` and any `copilot-*` resolve to
`CopilotBackend`. Bare `copilot-` **is accepted** (matching production). The
registry returns a plain `CopilotBackend` and does **not** collapse the name to
canonical `copilot` — the original alias (e.g. `copilot-gemma4`) travels in
`AgentInvocation.runner`, and `CopilotBackend.invoke` forwards it as
`cli_cmd=<runner>`.

## Configured openai-compat alias

Config-alias runners (`provider="openai-compat"` aliases, distinct from the
built-in `openai-compat` runner) are resolved only when an alias predicate is
injected — **inject-to-enable**. A bare `RunnerRegistry()` resolves only
built-ins and never imports `core` at import or resolve time.

```python
reg = RunnerRegistry(supports_openai_compat_alias=my_predicate)
backend = reg.resolve("some-alias")   # -> OpenAICompatAliasBackend
```

The returned `OpenAICompatAliasBackend` re-validates the name and carries the
original alias via `AgentInvocation.runner` at `invoke()` time.

## Selection precedence

Faithful to `core.run_cmds._dispatch_agent_cmd`:

1. **copilot family** — `copilot` or `copilot-*`.
2. **exact built-in** (or an injected custom factory key).
3. **injected openai-compat alias** predicate.
4. otherwise `UnknownRunnerError`.

Built-ins (including the copilot family) are matched **before** the alias
predicate is ever consulted, so a built-in wins even if the predicate would
accept the name. The predicate is never called for malformed input.

## Name handling

Matching is case-insensitive (`runner.lower()`, mirroring dispatch). Names are
**not** stripped — dispatch does not `strip()`.

| input             | outcome                         |
| ----------------- | ------------------------------- |
| `"Claude"`        | resolves as `claude`            |
| `" claude"`       | `UnknownRunnerError` (no trim)  |
| `"claude "`       | `UnknownRunnerError` (no trim)  |
| `"   "` / `"\t"`  | `InvalidRunnerNameError`        |
| `""`              | `InvalidRunnerNameError`        |
| non-`str` / `None`| `InvalidRunnerNameError`        |

## Structured errors

All selection errors subclass `RunnerSelectionError` (itself a
`RunnerBackendError`) and carry safe identity fields only — never prompt,
metadata, environment, or credential payload.

- `UnknownRunnerError` — `.requested`, `.normalized`, `.supported` (the built-in
  tuple). The message names the requested name and the supported built-ins; it
  must not imply arbitrary aliases are supported.
- `InvalidRunnerNameError` — `.requested` (may be non-`str`/`None`).
- `RegistryConfigurationError` — bad configuration or a misbehaving factory /
  predicate.

## Factory model

Custom factories **override** the matching built-in and add new resolvable keys;
they never delete the five built-ins. A single override (e.g.
`{"claude": fake}`) therefore cannot orphan `copilot` or the others.

The configuration is validated at construction:

- `factories` must be a `Mapping` or `None`;
- factory keys must be non-empty strings (stored lowercased);
- factory values must be callable;
- `supports_openai_compat_alias` must be callable or `None`.

Failures at resolve time — a factory that raises, a factory returning a
non-`RunnerBackend`, or an alias predicate that raises — produce
`RegistryConfigurationError` with the original exception preserved as
`__cause__`. No raw `KeyError` or incidental `TypeError` escapes.
`KeyboardInterrupt` / `SystemExit` always propagate unwrapped.

## Fresh-instance semantics

No caching: each `resolve` call invokes the selected factory and returns a fresh
backend instance. (An injected factory that intentionally returns the same
object is identity injection, not a cache.)

## Leaf-safety

`runners.registry` imports only `runners.*` + stdlib `typing`. Neither importing
the module, constructing a `RunnerRegistry`, nor resolving a backend loads
anything from `core`, `workflow`, `server`, `eval`, `telemetry`, `opik`, or a
vendor SDK — backend adapters lazy-import their `core.run_cmds` callable inside
`invoke`. No optional vendor package is required.

## Example

```python
from runners import RunnerRegistry

reg = RunnerRegistry()
backend = reg.resolve("claude")       # -> ClaudeBackend
result = backend.invoke(invocation)   # lazy-imports core.run_cmds here
```

## What it does NOT do

No failover / retry, no fallback ordering, no lifecycle events, no artifact
contracts, no production dispatch migration. A future prompt relates the
registry to failover isolation and the workflow-dispatch migration.
