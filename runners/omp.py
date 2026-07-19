"""Prompt 14 — built-in openai-compat runner backend adapter.

`BuiltinOpenAICompatBackend` delegates to `core.run_cmds.run_omp_cmd(prompt,
agent, model, extra_skills, repo, change_id, ...) -> str` — the built-in
`openai-compat` runner, of which omp (oh-my-pi) is the implementation detail.
It handles only the literal runner name `"openai-compat"`; config alias runners
belong to `runners.openai_compat.OpenAICompatAliasBackend`.

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


class BuiltinOpenAICompatBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_omp_cmd`."""

    def __init__(self, backend: Callable[..., str] | None = None) -> None:
        self._backend = backend

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        if invocation.runner.lower() != "openai-compat":
            raise UnsupportedInvocationError(
                "BuiltinOpenAICompatBackend only handles the literal runner "
                f"'openai-compat', got {invocation.runner!r}",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, COMMON_UNSUPPORTED_CONTROLS)

        # `run_omp_cmd`'s `model` default is already `None`, so passing the
        # invocation's model (even `None`) is faithful — no omit-when-None dance.
        kwargs: dict[str, object] = {
            "prompt": prompt,
            "agent": invocation.agent,
            "model": invocation.model,
        }
        skills = extra_skills(invocation)
        if skills is not None:
            kwargs["extra_skills"] = skills
        if invocation.repo is not None:
            kwargs["repo"] = str(invocation.repo)
        if invocation.change_id is not None:
            kwargs["change_id"] = invocation.change_id

        backend = self._backend
        if backend is None:
            from core.run_cmds import run_omp_cmd  # lazy: only at call time

            backend = run_omp_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
