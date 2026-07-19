"""Prompt 14 — Claude runner backend adapter.

`ClaudeBackend` delegates to `core.run_cmds.run_claude_cmd(prompt, agent,
model, ...) -> str`, wrapping the bare-`str` return into a `SUCCEEDED`
`AgentResult`. Module scope imports only `runners.*` + stdlib; the default
backend callable is lazy-imported inside `invoke`, so importing this module
loads no `core`.
"""

from __future__ import annotations

from typing import Callable

from runners._adapter_support import (
    COMMON_UNSUPPORTED_CONTROLS,
    call_backend,
    reject_extra_skills,
    reject_unsupported,
    require_prompt,
    succeeded_result,
)
from runners.base import RunnerBackend, UnsupportedInvocationError
from runners.models import AgentInvocation, AgentResult

# Claude has no `repo`/`change_id`/`extra_skills` parameters, plus the common
# four execution controls with no faithful equivalent.
_UNSUPPORTED_CONTROLS = ("repo", "change_id", *COMMON_UNSUPPORTED_CONTROLS)


class ClaudeBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_claude_cmd`."""

    def __init__(self, backend: Callable[..., str] | None = None) -> None:
        # Inject for tests; `None` -> lazy default at call time.
        self._backend = backend

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        if invocation.runner.lower() != "claude":
            raise UnsupportedInvocationError(
                f"ClaudeBackend only handles runner 'claude', got {invocation.runner!r}",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, _UNSUPPORTED_CONTROLS)
        reject_extra_skills(invocation)

        kwargs: dict[str, object] = {"prompt": prompt, "agent": invocation.agent}
        # Omit-when-None: `run_claude_cmd`'s `model` default is a real model
        # string, so passing `None` would override it with a wrong CLI flag.
        if invocation.model is not None:
            kwargs["model"] = invocation.model

        backend = self._backend
        if backend is None:
            from core.run_cmds import run_claude_cmd  # lazy: only at call time

            backend = run_claude_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
