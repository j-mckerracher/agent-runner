"""Prompt 14 — Gemini runner backend adapter.

`GeminiBackend` delegates to `core.run_cmds.run_gemini_cmd(prompt, agent,
model, extra_skills, ...) -> str`, wrapping the bare-`str` return into a
`SUCCEEDED` `AgentResult`. Module scope imports only `runners.*` + stdlib; the
default backend callable is lazy-imported inside `invoke`, so importing this
module loads no `core`.
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

# Gemini takes `extra_skills` but no `repo`/`change_id`.
_UNSUPPORTED_CONTROLS = ("repo", "change_id", *COMMON_UNSUPPORTED_CONTROLS)


class GeminiBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_gemini_cmd`."""

    def __init__(self, backend: Callable[..., str] | None = None) -> None:
        self._backend = backend

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        if invocation.runner.lower() != "gemini":
            raise UnsupportedInvocationError(
                f"GeminiBackend only handles runner 'gemini', got {invocation.runner!r}",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, _UNSUPPORTED_CONTROLS)

        kwargs: dict[str, object] = {"prompt": prompt, "agent": invocation.agent}
        if invocation.model is not None:
            kwargs["model"] = invocation.model
        skills = extra_skills(invocation)
        if skills is not None:
            kwargs["extra_skills"] = skills

        backend = self._backend
        if backend is None:
            from core.run_cmds import run_gemini_cmd  # lazy: only at call time

            backend = run_gemini_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
