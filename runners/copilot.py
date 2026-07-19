"""Prompt 14 — Copilot runner backend adapter.

`CopilotBackend` delegates to `core.run_cmds.run_copilot_cmd(prompt, agent,
model, cli_cmd, extra_skills, ...) -> str`, wrapping the bare-`str` return into
a `SUCCEEDED` `AgentResult`.

Two shapes, mirroring the legacy dispatcher:

- Base runner ``"copilot"`` — `cli_cmd="copilot"`, `model` passed when supplied
  (omit-when-None; the function's `model` default is a real string).
- Alias runner ``"copilot-<name>"`` — `cli_cmd=<runner>`; the alias binary *is*
  its own model configuration and accepts no `--model` flag, so a supplied
  `model` is **rejected** rather than silently dropped.

Module scope imports only `runners.*` + stdlib; the default backend callable is
lazy-imported inside `invoke`, so importing this module loads no `core`.
"""

from __future__ import annotations

from typing import Callable

from runners._adapter_support import (
    COMMON_UNSUPPORTED_CONTROLS,
    call_backend,
    extra_skills,
    reject_unsupported,
    require_prompt,
    succeeded_result,
)
from runners.base import RunnerBackend, UnsupportedInvocationError
from runners.models import AgentInvocation, AgentResult

# Copilot takes `cli_cmd` + `extra_skills` but no `repo`/`change_id`.
_UNSUPPORTED_CONTROLS = ("repo", "change_id", *COMMON_UNSUPPORTED_CONTROLS)


class CopilotBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_copilot_cmd`."""

    def __init__(self, backend: Callable[..., str] | None = None) -> None:
        self._backend = backend

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        runner_lower = invocation.runner.lower()
        is_base = runner_lower == "copilot"
        is_alias = runner_lower.startswith("copilot-")
        if not (is_base or is_alias):
            raise UnsupportedInvocationError(
                "CopilotBackend only handles runner 'copilot' or a 'copilot-<name>' "
                f"alias, got {invocation.runner!r}",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, _UNSUPPORTED_CONTROLS)

        kwargs: dict[str, object] = {"prompt": prompt, "agent": invocation.agent}
        if is_base:
            kwargs["cli_cmd"] = "copilot"
            # Omit-when-None: the function's `model` default is a real string.
            if invocation.model is not None:
                kwargs["model"] = invocation.model
        else:
            # Alias binary IS the command and defines its own model; a supplied
            # `model` cannot be honored, so reject rather than drop it.
            if invocation.model is not None:
                raise UnsupportedInvocationError(
                    f"copilot alias runner {invocation.runner!r} accepts no model; "
                    "the alias binary defines its own model",
                    agent=invocation.agent,
                    runner=invocation.runner,
                    model=invocation.model,
                )
            kwargs["cli_cmd"] = invocation.runner
        skills = extra_skills(invocation)
        if skills is not None:
            kwargs["extra_skills"] = skills

        backend = self._backend
        if backend is None:
            from core.run_cmds import run_copilot_cmd  # lazy: only at call time

            backend = run_copilot_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
