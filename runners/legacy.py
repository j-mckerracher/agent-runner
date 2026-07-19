"""Prompt 13 — legacy dispatch adapter.

`LegacyDispatchBackend` proves an `AgentInvocation` can flow through the new
`RunnerBackend` seam by delegating to the existing
`core.agent_cmd.run_agent_cmd(runner, prompt, agent, **kwargs) -> str`. It is
an abstraction seam around *existing* behavior: `run_agent_cmd` is untouched
(same params, same `-> str` return, same failover, same exceptions).

Isolation: `core.agent_cmd` is **not** imported at module scope. The default
dispatcher is imported lazily inside `invoke`, so `from runners.legacy import
LegacyDispatchBackend` loads no `core`; the legacy stack is pulled in only on
the first default-path call. Tests inject a fake dispatcher via the
constructor, exercising the adapter without importing `core` at all.
"""

from __future__ import annotations

from typing import Any, Callable

from runners.base import (
    RunnerBackend,
    RunnerInvocationError,
    UnsupportedInvocationError,
)
from runners.models import AgentInvocation, AgentResult, AgentResultStatus

# Execution controls with no faithful legacy equivalent. Rejected on
# *presence* (`is not None`), not truthiness, so an explicit empty
# `env_overrides={}` / `allowed_tools=()` is still rejected: the caller
# supplied the control, and silently dropping it would mislead while
# forwarding it would raise `TypeError` in `run_agent_cmd`.
_UNSUPPORTED_CONTROLS = ("timeout_s", "working_dir", "env_overrides", "allowed_tools")


class LegacyDispatchBackend(RunnerBackend):
    """`RunnerBackend` adapter over `core.agent_cmd.run_agent_cmd`."""

    def __init__(self, dispatcher: Callable[..., str] | None = None) -> None:
        # Inject for tests; no registry, no discovery. `None` -> lazy default.
        self._dispatcher = dispatcher

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        prompt = self._require_prompt(invocation)
        self._reject_unsupported(invocation)

        kwargs: dict[str, Any] = {}
        if invocation.model is not None:
            kwargs["runner_model"] = invocation.model
        if invocation.repo is not None:
            # Legacy `repo` is declared `str | None`; honor that type.
            kwargs["repo"] = str(invocation.repo)
        if invocation.change_id is not None:
            kwargs["change_id"] = invocation.change_id
        extra_skills = self._extra_skills(invocation)
        if extra_skills is not None:
            kwargs["extra_skills"] = extra_skills

        dispatcher = self._dispatcher
        if dispatcher is None:
            from core.agent_cmd import run_agent_cmd  # lazy: only at call time

            dispatcher = run_agent_cmd

        try:
            response_text = dispatcher(
                runner=invocation.runner,
                prompt=prompt,
                agent=invocation.agent,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately wrap dispatch failures
            # KeyboardInterrupt / SystemExit are BaseException, not Exception:
            # they propagate unconverted.
            raise RunnerInvocationError(
                self._dispatch_error_message(invocation),
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            ) from exc

        return AgentResult(
            status=AgentResultStatus.SUCCEEDED,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
            response_text=response_text,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _require_prompt(invocation: AgentInvocation) -> str:
        if invocation.prompt is not None:
            return invocation.prompt
        raise UnsupportedInvocationError(
            "legacy adapter requires materialized prompt text; prompt_ref cannot be resolved",
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        )

    @staticmethod
    def _reject_unsupported(invocation: AgentInvocation) -> None:
        supplied = [name for name in _UNSUPPORTED_CONTROLS if getattr(invocation, name) is not None]
        if supplied:
            raise UnsupportedInvocationError(
                "legacy adapter cannot honor execution control(s): " + ", ".join(supplied),
                agent=invocation.agent,
                runner=invocation.runner,
                model=invocation.model,
            )

    @staticmethod
    def _extra_skills(invocation: AgentInvocation) -> list[str] | None:
        if "extra_skills" not in invocation.metadata:
            return None
        value = invocation.metadata["extra_skills"]
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return list(value)
        raise UnsupportedInvocationError(
            "metadata['extra_skills'] must be a list/tuple of str",
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        )

    @staticmethod
    def _dispatch_error_message(invocation: AgentInvocation) -> str:
        # Identity only — never prompt text, metadata, env values, or secrets.
        parts = [f"agent={invocation.agent!r}", f"runner={invocation.runner!r}"]
        if invocation.model is not None:
            parts.append(f"model={invocation.model!r}")
        return "legacy dispatch failed for " + ", ".join(parts)
