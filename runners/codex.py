"""Prompt 14 — Codex runner backend adapter.

`CodexBackend` delegates to `core.run_cmds.run_codex_cmd(prompt, agent, model,
extra_skills, repo, change_id, ...) -> str`, wrapping the bare-`str` return
into a `SUCCEEDED` `AgentResult`. Module scope imports only `runners.*` +
stdlib; the default backend callable is lazy-imported inside `invoke`, so
importing this module loads no `core`.
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


class CodexBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_codex_cmd`."""

    def __init__(self, backend: Callable[..., str] | None = None) -> None:
        self._backend = backend

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        if invocation.runner.lower() != "codex":
            raise UnsupportedInvocationError(
                f"CodexBackend only handles runner 'codex', got {invocation.runner!r}",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, COMMON_UNSUPPORTED_CONTROLS)

        kwargs: dict[str, object] = {"prompt": prompt, "agent": invocation.agent}
        if invocation.model is not None:
            kwargs["model"] = invocation.model
        skills = extra_skills(invocation)
        if skills is not None:
            kwargs["extra_skills"] = skills
        if invocation.repo is not None:
            # `run_codex_cmd`'s `repo` is declared `str | None`; honor that type.
            kwargs["repo"] = str(invocation.repo)
        if invocation.change_id is not None:
            kwargs["change_id"] = invocation.change_id

        backend = self._backend
        if backend is None:
            from core.run_cmds import run_codex_cmd  # lazy: only at call time

            backend = run_codex_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
