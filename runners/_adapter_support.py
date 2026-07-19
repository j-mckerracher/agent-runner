"""Prompt 14 — shared private helpers for the concrete runner adapters.

Small, identical mechanics factored out of the six family adapters
(`runners.claude`, `runners.codex`, `runners.gemini`, `runners.copilot`,
`runners.omp`, `runners.openai_compat`). Not a framework and **not** exported
from the package root — a leaf-safe helper importing only stdlib plus
`runners.base` / `runners.models`, so importing any adapter never loads
`core`, `workflow`, `server`, or a vendor SDK.

`runners.legacy` is deliberately left untouched (it predates this helper); the
slight duplication with its private methods is accepted to keep the legacy
adapter frozen.
"""

from __future__ import annotations

from typing import Any, Callable

from runners.base import RunnerInvocationError, UnsupportedInvocationError
from runners.models import AgentInvocation, AgentResult, AgentResultStatus

# Execution controls no CLI-family adapter can honor faithfully. Rejected on
# *presence* (`is not None`), not truthiness, so an explicit empty
# `env_overrides={}` / `allowed_tools=()` is still rejected: the caller
# supplied the control, and silently dropping it would mislead while
# forwarding it would raise `TypeError` in the backend function.
COMMON_UNSUPPORTED_CONTROLS = ("timeout_s", "working_dir", "env_overrides", "allowed_tools")


def require_prompt(invocation: AgentInvocation) -> str:
    """Return the materialized prompt text, or reject a `prompt_ref`-only call.

    The message never contains the prompt text — only invocation identity.
    """
    if invocation.prompt is not None:
        return invocation.prompt
    raise UnsupportedInvocationError(
        "adapter requires materialized prompt text; prompt_ref cannot be resolved",
        agent=invocation.agent,
        runner=invocation.runner,
        model=invocation.model,
    )


def reject_unsupported(invocation: AgentInvocation, names: tuple[str, ...]) -> None:
    """Raise if any control in `names` is *present* (`is not None`).

    The message lists only field names and identity — never the values.
    """
    supplied = [name for name in names if getattr(invocation, name) is not None]
    if supplied:
        raise UnsupportedInvocationError(
            "adapter cannot honor execution control(s): " + ", ".join(supplied),
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        )


def reject_extra_skills(invocation: AgentInvocation) -> None:
    """Raise if `metadata["extra_skills"]` is present (for families without it)."""
    if "extra_skills" in invocation.metadata:
        raise UnsupportedInvocationError(
            "adapter cannot honor metadata['extra_skills']",
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        )


def extra_skills(invocation: AgentInvocation) -> list[str] | None:
    """Return a validated list-of-str from `metadata["extra_skills"]`, else `None`.

    A present-but-invalid value (not a list/tuple of `str`) is a requested-but-
    unusable control, so it raises rather than being silently dropped.
    """
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


def dispatch_error_message(invocation: AgentInvocation) -> str:
    """Identity-only failure message — never prompt text, metadata, or secrets."""
    parts = [f"agent={invocation.agent!r}", f"runner={invocation.runner!r}"]
    if invocation.model is not None:
        parts.append(f"model={invocation.model!r}")
    return "runner dispatch failed for " + ", ".join(parts)


def call_backend(
    invocation: AgentInvocation,
    backend: Callable[..., str],
    /,
    **kwargs: Any,
) -> str:
    """Invoke `backend` exactly once and return its bare-`str` result.

    - Any `Exception` from the backend is wrapped in a `RunnerInvocationError`
      carrying invocation identity, with the original preserved via `__cause__`.
    - `KeyboardInterrupt` / `SystemExit` (`BaseException`) propagate unconverted.
    - A non-`str` return is rejected (identity + `type(result).__name__` only —
      never the value) so a bad value cannot reach `AgentResult.response_text`.
    """
    try:
        result = backend(**kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberately wrap dispatch failures
        raise RunnerInvocationError(
            dispatch_error_message(invocation),
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        ) from exc
    if not isinstance(result, str):
        raise RunnerInvocationError(
            "backend returned a non-str result: " + type(result).__name__,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
        )
    return result


def succeeded_result(invocation: AgentInvocation, response_text: str) -> AgentResult:
    """Build a `SUCCEEDED` result: identity + `response_text`, rest `None`.

    No telemetry is fabricated — the family functions return a bare `str` and
    emit tokens/cost/duration/exit code/session-log path as side effects only.
    """
    return AgentResult(
        status=AgentResultStatus.SUCCEEDED,
        agent=invocation.agent,
        runner=invocation.runner,
        model=invocation.model,
        response_text=response_text,
    )
