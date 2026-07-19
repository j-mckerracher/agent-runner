# Runner Backend Adapters (Prompt 14)

## Why this exists

Prompt 13 shipped the `RunnerBackend` execution seam
(`runners/base.py`) and exactly one adapter — `LegacyDispatchBackend`
(`runners/legacy.py`) — which delegates to `core.agent_cmd.run_agent_cmd`. It
deliberately did **not** provide per-family adapters, a registry, selection,
failover extraction, or workflow migration.

This prompt adds concrete `RunnerBackend` adapters for the runner families that
already exist in the repo. Each adapter delegates to the smallest existing
backend execution function in `core.run_cmds`, converts its bare-`str` return
into an `AgentResult`, and establishes a per-backend adapter seam **without**
migrating production workflow dispatch. Legacy dispatch and all existing
backend behavior stay untouched.

```
AgentInvocation ──► <Family>Backend.invoke ──► run_<family>_cmd(...) -> str ──► AgentResult
    (typed)             (this prompt)               (unchanged core)             (typed)
```

## Available adapters

| Adapter class                 | Module                    | Runner name(s)          | Delegates to                     |
| ----------------------------- | ------------------------- | ----------------------- | -------------------------------- |
| `ClaudeBackend`               | `runners.claude`          | `claude`                | `run_claude_cmd`                 |
| `CodexBackend`                | `runners.codex`           | `codex`                 | `run_codex_cmd`                  |
| `GeminiBackend`               | `runners.gemini`          | `gemini`                | `run_gemini_cmd`                 |
| `CopilotBackend`              | `runners.copilot`         | `copilot`, `copilot-*`  | `run_copilot_cmd`                |
| `BuiltinOpenAICompatBackend`  | `runners.omp`             | `openai-compat` (literal) | `run_omp_cmd`                  |
| `OpenAICompatAliasBackend`    | `runners.openai_compat`   | config openai-compat aliases | `run_openai_compat_cmd`     |

The six classes are re-exported from the package root
(`from runners import ClaudeBackend, ...`). `LegacyDispatchBackend` stays
submodule-only (`from runners.legacy import LegacyDispatchBackend`), unchanged.

### Two openai-compat paths, not one

`BuiltinOpenAICompatBackend` and `OpenAICompatAliasBackend` mirror the two
distinct dispatch paths in `core.run_cmds._dispatch_agent_cmd`, not two
unrelated families:

- **Built-in** `openai-compat` runner → `run_omp_cmd` (omp/oh-my-pi is its
  implementation detail). Handled only for the *literal* runner name
  `"openai-compat"`.
- **Config aliases** (runner names configured with `provider="openai-compat"`)
  → `run_openai_compat_cmd`.

## Per-adapter invocation mapping

`prompt` and `agent` are always forwarded. `model` handling and the extra
controls differ per family, matching the backend function signatures.

| Adapter                      | `model` handling                          | `repo` | `change_id` | `extra_skills` | extra kwargs                    |
| ---------------------------- | ----------------------------------------- | :----: | :---------: | :------------: | ------------------------------- |
| `ClaudeBackend`              | omit-when-`None`                          |   ✗    |     ✗       |      ✗         | —                               |
| `CodexBackend`               | omit-when-`None`                          |   ✓    |     ✓       |      ✓         | —                               |
| `GeminiBackend`              | omit-when-`None`                          |   ✗    |     ✗       |      ✓         | —                               |
| `CopilotBackend` (base)      | omit-when-`None`                          |   ✗    |     ✗       |      ✓         | `cli_cmd="copilot"`             |
| `CopilotBackend` (alias)     | **rejected** if supplied                  |   ✗    |     ✗       |      ✓         | `cli_cmd=<runner>`              |
| `BuiltinOpenAICompatBackend` | passed through (incl. `None`)             |   ✓    |     ✓       |      ✓         | —                               |
| `OpenAICompatAliasBackend`   | `model = inv.model or inv.runner`         |   ✓    |     ✓       |      ✓         | `runner=inv.runner`             |

- **omit-when-`None`** — the family function's `model` default is a real model
  string, so passing `None` would override it with a wrong CLI flag. The kwarg
  is omitted when `invocation.model is None`.
- **passed through** — `run_omp_cmd`'s `model` default is already `None`, so
  the value (including `None`) is forwarded faithfully.
- **`repo`** is forwarded as `str(invocation.repo)` (the backend functions
  declare `repo: str | None`).

### Copilot: base vs alias

- Base runner `"copilot"` → `cli_cmd="copilot"`, `model` passed when supplied.
- Alias runner `"copilot-<name>"` → `cli_cmd=<runner>`. The alias binary *is*
  its own model configuration and accepts no `--model` flag, so a supplied
  `model` is **rejected** with `UnsupportedInvocationError` rather than silently
  dropped.

### `OpenAICompatAliasBackend`: runner validation

Because this adapter bypasses `_dispatch_agent_cmd`, it cannot assume an
arbitrary runner is an openai-compat alias. It validates the runner via a
`supports_runner(runner) -> bool` predicate:

- Constructor: `OpenAICompatAliasBackend(backend=None, supports_runner=None)`.
- A `False` result (e.g. `"claude"`, `"codex"`, an unknown name) raises
  `UnsupportedInvocationError` **before** the backend is called.
- The default predicate is lazy-imported inside `invoke` (resolves the runner's
  provider via `core.runner_models._provider_for_runner`, best-effort config
  load) so importing the module stays `core`-free. Tests inject a deterministic
  predicate.

## Supported vs rejected controls

Every adapter rejects the four execution controls that have no faithful CLI
equivalent, on **presence** (`is not None`) — not truthiness. An explicit empty
`env_overrides={}` or `allowed_tools=()` is still rejected, because the caller
supplied the control:

```
timeout_s   working_dir   env_overrides   allowed_tools
```

Families that lack a parameter also reject it on presence: `ClaudeBackend`,
`GeminiBackend`, and `CopilotBackend` reject `repo`/`change_id`;
`ClaudeBackend` additionally rejects `metadata["extra_skills"]`.

An `extra_skills` value that is present but not a list/tuple of `str` is a
requested-but-unusable control, so it raises rather than being dropped.

### `prompt` vs `prompt_ref`

Each adapter requires materialized `invocation.prompt`. A `prompt_ref`-only
invocation raises `UnsupportedInvocationError` — this prompt invents no ref
loader (no file reading, no resolution).

### Deliberately unconsumed

`run_id` and `trace_context` are identity/observability, not execution
controls, so they are ignored — matching `LegacyDispatchBackend` (documented,
not silently dropped). All non-`extra_skills` metadata keys are ignored:
`metadata` is a free-form annotation bag and is never forwarded as `**kwargs`.

## Result fidelity — no fabricated telemetry

Every family function returns a bare `str` (the final text). Token counts,
cost, duration, exit code, and session-log path are emitted as **side effects**
(`_emit_llm_call_event`, `_write_cli_session_log`) — never returned. So on
success the adapter populates only:

```
status = SUCCEEDED
agent, runner, model   (identity, from the invocation)
response_text          (the returned str)
```

Everything else — `stdout`, `stderr`, `exit_code`, `duration_ms`, `tokens_in`,
`tokens_out`, `cost_usd`, `error_type`, `error_message`, `retry`,
`failover_attempts`, `artifacts_touched`, `session_log_ref` — stays `None`.
Missing telemetry is never zeroed or invented (see
`docs/agent-invocation-contract.md`).

## Exception behavior

The family functions always **raise** on failure (`ValueError`,
`subprocess.CalledProcessError`, `RunnerCommandError`, `RuntimeError`); there is
no completed-but-failed return value. Adapters therefore:

- Success → `AgentResult(status=SUCCEEDED, ...)`.
- Any `Exception` from the backend → `RunnerInvocationError` (identity-bearing,
  original preserved via `__cause__`). No adapter emits `FAILED`/`TIMED_OUT`.
- `KeyboardInterrupt` / `SystemExit` (`BaseException`) → propagate unconverted.
- A non-`str` return (`None`, `bytes`, etc.) → `RunnerInvocationError`
  (message carries `type(result).__name__` only, never the value), so an
  invalid value can't slip into `AgentResult.response_text` undetected.

Error messages carry only invocation identity (`agent`/`runner`/`model`) —
never prompt text, metadata, environment values, or credentials.

Internal retries stay owned by the backends (Copilot 5×, Gemini 5×,
openai-compat transport-level). Adapters call the function exactly **once** and
add no retries of their own.

## Import isolation

Each adapter module imports only `runners.base`, `runners.models`,
`runners._adapter_support`, and stdlib at module scope. The default backend
callable (and, for the alias adapter, the default `supports_runner` predicate)
is lazy-imported inside `invoke`, so `from runners.<family> import <Backend>`
loads no `core`, `workflow`, `server`, `eval`, `telemetry`, `opik`, or vendor
SDK, and starts no subprocess. `runners/_adapter_support.py` is a leaf helper —
shared identical mechanics, not exported from the package root.

## Why legacy remains

`LegacyDispatchBackend` and `run_agent_cmd` are untouched: this prompt adds
per-backend seams *beside* the legacy path, it does not migrate production
dispatch onto them. The slight duplication between `_adapter_support.py` and the
legacy adapter's private methods is accepted to keep the legacy adapter frozen.

## Deferred work

Out of scope here: a runner registry, backend selection / config loading,
migrating workflow/CLI/eval/server dispatch onto these adapters, extracting
failover into the backend, retry ownership, lifecycle events, prompt hashing,
`ArtifactRef`, prompt-ref file resolution, and async/stream/batch execution.
