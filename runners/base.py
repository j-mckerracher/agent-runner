"""Prompt 13 — RunnerBackend execution seam (interface + errors).

Defines the runner-neutral execution boundary: a `RunnerBackend` takes one
`AgentInvocation` and produces one `AgentResult`, or raises. This module is a
stdlib-only leaf (plus the sibling `runners.models` contracts): it imports
nothing from `core`, `workflow`, `server`, `eval`, `telemetry`, `opik`, or
any vendor SDK, so merely importing the interface never loads the legacy
execution stack.

Failure rule: `invoke` **always raises** a `RunnerInvocationError` (or a
subclass) when it cannot produce a trustworthy `AgentResult` — it never
returns a `FAILED` result to signal an execution failure. Concrete backends
whose transport *does* carry a real exit code may still construct a
`FAILED`/`TIMED_OUT` result for a completed-but-unsuccessful run; the rule
only forbids masking a *dispatch* failure as a returned result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from runners.models import AgentInvocation, AgentResult


class RunnerBackendError(RuntimeError):
    """Base error for the runner backend boundary.

    Carries the invocation identity (`agent`/`runner`/`model`) so callers and
    logs have context without the error message ever needing to include the
    prompt text, metadata, environment values, or credentials.
    """

    def __init__(
        self,
        message: str,
        *,
        agent: str | None = None,
        runner: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.agent = agent
        self.runner = runner
        self.model = model


class RunnerInvocationError(RunnerBackendError):
    """`invoke` could not produce a trustworthy `AgentResult`.

    Raised when the underlying dispatch failed. The originating exception is
    preserved via `raise ... from exc` (`__cause__`).
    """


class UnsupportedInvocationError(RunnerInvocationError):
    """A pre-flight rejection: the backend cannot honor this invocation.

    Subclasses `RunnerInvocationError` so the invariant "`invoke` raises
    `RunnerInvocationError` (or a subclass) whenever it cannot produce a
    trustworthy result" holds literally, whether the failure is detected
    before or during dispatch. Raised when the invocation requests a control
    the backend has no faithful way to honor (rather than silently dropping
    it or forwarding it into an error).
    """


class RunnerBackend(ABC):
    """Runner-neutral execution boundary: one invocation -> one result.

    Implementations translate an `AgentInvocation` into a concrete execution
    and return an `AgentResult`, or raise `RunnerInvocationError` (or a
    subclass). The interface is intentionally minimal — no registry, no
    selection, no lifecycle — those belong to later prompts.
    """

    @abstractmethod
    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        """Execute `invocation` and return its `AgentResult`.

        Raises `RunnerInvocationError` (or a subclass, e.g.
        `UnsupportedInvocationError`) when a trustworthy result cannot be
        produced. Must never return a result to signal a dispatch failure.
        """
        raise NotImplementedError
