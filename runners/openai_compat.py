"""Prompt 14 — openai-compat *alias* runner backend adapter.

`OpenAICompatAliasBackend` delegates to
`core.run_cmds.run_openai_compat_cmd(prompt, agent, model, runner,
extra_skills, repo, change_id, ...) -> str`, wrapping the bare-`str` return
into a `SUCCEEDED` `AgentResult`. This is the *config alias* family — runner
names configured as `provider="openai-compat"` aliases (distinct from the
built-in `"openai-compat"` runner, which `runners.omp.BuiltinOpenAICompatBackend`
handles via `run_omp_cmd`).

Because this adapter bypasses `core.run_cmds._dispatch_agent_cmd`, it cannot
assume an arbitrary runner is an openai-compat alias: it validates the runner
via `supports_runner(runner)` and raises `UnsupportedInvocationError` before
touching the backend when the name is not a recognized alias.

Module scope imports only `runners.*` + stdlib; both the default backend
callable and the default `supports_runner` predicate are lazy-imported inside
`invoke`, so importing this module loads no `core`.
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


def _default_supports_runner(runner: str) -> bool:
    """Whether `runner` resolves to an existing openai-compat alias.

    Lazy so module import stays `core`-free. Mirrors the dispatcher's fallback:
    load config (best-effort) and resolve the provider for the runner name.
    """
    from core.runner_models import _provider_for_runner

    config: dict | None = None
    try:
        from server.config import load_config

        config = load_config()
    except Exception:  # noqa: BLE001 - config load is best-effort, as in dispatch
        config = None
    return _provider_for_runner(runner, config=config) == "openai-compat"


class OpenAICompatAliasBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.run_cmds.run_openai_compat_cmd`."""

    def __init__(
        self,
        backend: Callable[..., str] | None = None,
        supports_runner: Callable[[str], bool] | None = None,
    ) -> None:
        # Inject both for tests; `None` -> lazy defaults at call time.
        self._backend = backend
        self._supports_runner = supports_runner

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        prompt = require_prompt(invocation)
        reject_unsupported(invocation, COMMON_UNSUPPORTED_CONTROLS)

        supports_runner = self._supports_runner
        if supports_runner is None:
            supports_runner = _default_supports_runner
        if not supports_runner(invocation.runner):
            raise UnsupportedInvocationError(
                f"runner {invocation.runner!r} is not a recognized openai-compat alias",
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )

        # Mirror the dispatcher default: model = inv.model or inv.runner.
        kwargs: dict[str, object] = {
            "prompt": prompt,
            "agent": invocation.agent,
            "model": invocation.model or invocation.runner,
            "runner": invocation.runner,
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
            from core.run_cmds import run_openai_compat_cmd  # lazy: only at call time

            backend = run_openai_compat_cmd

        response_text = call_backend(invocation, backend, **kwargs)
        return succeeded_result(invocation, response_text)
