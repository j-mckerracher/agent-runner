"""Prompt 16 — runner-layer failover seam (leaf-safe policy boundary).

Composes ``AgentInvocation -> FailoverExecutor -> RunnerRegistry ->
RunnerBackend.invoke -> AgentResult``, executing an explicit ordered route of
backend attempts and recording ordered ``FailoverMetadata`` hops. Failover
policy lives *here* — not inside workflow stages or individual adapters — and
the live ``core.run_cmds`` path is untouched. Provider-specific quota-string
parsing stays in ``core.runner_failover``; this seam decides eligibility from
structured ``AgentResult`` / ``RunnerBackendError`` values via an injected
predicate.

Leaf-safe: imports only stdlib (``dataclasses``/``typing``) plus sibling
``runners.base``/``runners.models``/``runners.registry``. Importing this module
or constructing a ``FailoverExecutor`` loads nothing from ``core``,
``workflow``, ``server``, ``eval``, ``telemetry``, ``opik``, or any vendor SDK:
the default registry builds no backends (its factories are lazy) and the
adapters lazy-import their ``core.run_cmds`` callable inside ``invoke``.

Transition-only metadata: ``failover_attempts`` records one ``FailoverMetadata``
per *hop* from a failed route to the next. The final attempted runner/model
(the terminal failure with no destination) is not a hop record — it is carried
separately on ``FailoverExhaustedError``. So for N failed routes there are N-1
hop records and total attempts == ``len(failover_attempts) + 1``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Protocol, runtime_checkable

from runners.base import RunnerBackendError
from runners.models import (
    AgentInvocation,
    AgentResult,
    AgentResultStatus,
    FailoverMetadata,
)
from runners.registry import RunnerRegistry, _normalize

__all__ = [
    "FailoverConfigurationError",
    "FailoverExecutor",
    "FailoverExhaustedError",
    "FailoverMetadataConflictError",
    "FailoverPlan",
    "RunnerRoute",
    "default_failover_eligibility",
]


# --- Errors ------------------------------------------------------------------


class FailoverConfigurationError(ValueError):
    """Build/argument-time fault: invalid route/plan or invalid ``execute``
    argument types (§10).

    Mirrors ``AgentContractError(ValueError)`` style — a ``ValueError`` rather
    than a raw ``RuntimeError``/``KeyError``/``TypeError`` — so callers can
    distinguish bad configuration from a runtime dispatch failure.
    """


class FailoverMetadataConflictError(RunnerBackendError):
    """Execution-time composition conflict (§6): a backend returned its own
    ``failover_attempts`` while the runner layer also recorded hops.

    Runtime boundary conflict, not invalid user input — hence a
    ``RunnerBackendError`` (carrying safe ``agent``/``runner``/``model``
    identity) rather than a ``FailoverConfigurationError``. Raised instead of
    silently overwriting backend-provided metadata.
    """


class FailoverExhaustedError(RunnerBackendError):
    """Every eligible route was attempted and none succeeded.

    Carries the ordered ``failover_attempts`` *transitions* and the terminal
    ``final_result`` (the last failed ``AgentResult``, or ``None`` when the last
    attempt raised). The message is a safe summary — attempt count plus the
    final runner name only — never prompt, environment, credential, or
    free-form text. ``final_result`` may still hold potentially sensitive
    diagnostic data (``response_text``/``stdout``/``stderr``/``error_message``/
    ``metadata``); it is deliberately excluded from the message and from the
    default ``__str__``/``__repr__`` rendering, so callers must render it via a
    redacted summary.

    Total attempts == ``len(failover_attempts) + 1``.
    """

    def __init__(
        self,
        message: str,
        *,
        failover_attempts: tuple[FailoverMetadata, ...],
        final_result: AgentResult | None,
        agent: str | None = None,
        runner: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message, agent=agent, runner=runner, model=model)
        self.failover_attempts = tuple(failover_attempts)
        self.final_result = final_result


# --- Route representation ----------------------------------------------------


@dataclass(frozen=True)
class RunnerRoute:
    """One ordered failover attempt: a runner name and an optional model.

    ``model=None`` means the attempt inherits the *original* invocation's model
    (see ``FailoverExecutor`` model-inheritance rule), never the model chosen by
    a prior route entry.
    """

    runner: str
    model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.runner, str) or not self.runner.strip():
            raise FailoverConfigurationError(
                f"runner: must be a nonempty, non-blank string, got {self.runner!r}"
            )
        if self.model is not None and not (
            isinstance(self.model, str) and len(self.model) > 0
        ):
            raise FailoverConfigurationError(
                f"model: must be a nonempty string or None, got {self.model!r}"
            )


class FailoverPlan:
    """An ordered, validated, duplicate-free route of ``RunnerRoute`` attempts.

    The constructor accepts an iterable of ``RunnerRoute`` or route mappings
    (``{"runner": ..., "model"?: ...}``), coercing mappings to ``RunnerRoute``.
    Validation rejects (never silently discards): an empty route, malformed
    entries, and duplicate identical attempts. Duplicate detection reuses
    ``runners.registry._normalize`` so failover dedup cannot drift from registry
    selection; the dedup key is ``(normalized_runner, route_model_as_specified)``.
    All violations raise ``FailoverConfigurationError``.
    """

    def __init__(self, entries: Iterable[RunnerRoute | Mapping[str, object]]) -> None:
        if isinstance(entries, (str, bytes, Mapping)):
            raise FailoverConfigurationError(
                "entries: must be an iterable of RunnerRoute / route mappings, "
                f"not a single {type(entries).__name__}"
            )
        try:
            raw = list(entries)
        except TypeError as exc:
            raise FailoverConfigurationError(
                f"entries: must be iterable, got {type(entries).__name__}"
            ) from exc
        if not raw:
            raise FailoverConfigurationError(
                "entries: failover route must contain at least one entry"
            )

        coerced = tuple(self._coerce(i, item) for i, item in enumerate(raw))

        seen: set[tuple[str, str | None]] = set()
        for route in coerced:
            key = (_normalize(route.runner), route.model)
            if key in seen:
                raise FailoverConfigurationError(
                    f"entries: duplicate route runner={route.runner!r} "
                    f"model={route.model!r}"
                )
            seen.add(key)

        self._entries = coerced

    @staticmethod
    def _coerce(index: int, item: object) -> RunnerRoute:
        if isinstance(item, RunnerRoute):
            return item
        if isinstance(item, Mapping):
            keys = set(item.keys())
            unknown = keys - {"runner", "model"}
            if unknown:
                raise FailoverConfigurationError(
                    f"entries[{index}]: unknown route keys {sorted(unknown)}"
                )
            if "runner" not in item:
                raise FailoverConfigurationError(
                    f"entries[{index}]: route mapping missing required 'runner' key"
                )
            try:
                return RunnerRoute(runner=item["runner"], model=item.get("model"))
            except FailoverConfigurationError as exc:
                raise FailoverConfigurationError(
                    f"entries[{index}]: {exc}"
                ) from exc
        raise FailoverConfigurationError(
            f"entries[{index}]: must be a RunnerRoute or route mapping, "
            f"got {type(item).__name__}"
        )

    @property
    def entries(self) -> tuple[RunnerRoute, ...]:
        return self._entries

    def __repr__(self) -> str:
        return f"FailoverPlan(entries={self._entries!r})"


# --- Eligibility -------------------------------------------------------------


@runtime_checkable
class FailoverEligibility(Protocol):
    """Predicate deciding whether an unsuccessful attempt is failover-eligible.

    Internal typing aid — not part of the exported public API. The executor
    only ever passes either ``result`` (a returned failure) or ``error`` (a
    caught ``RunnerBackendError``); it never surfaces ``KeyboardInterrupt`` /
    ``SystemExit`` (re-raised first) or arbitrary programmer errors (never
    caught).
    """

    def __call__(
        self,
        *,
        invocation: AgentInvocation,
        result: AgentResult | None,
        error: RunnerBackendError | None,
        attempt_index: int,
    ) -> bool: ...


def default_failover_eligibility(
    *,
    invocation: AgentInvocation,
    result: AgentResult | None,
    error: RunnerBackendError | None,
    attempt_index: int,
) -> bool:
    """Conservative default eligibility.

    Continue on any ``RunnerBackendError``; continue on an ``AgentResult`` whose
    status is ``FAILED`` or ``TIMED_OUT``; otherwise ``False``. No
    provider-specific quota-string parsing — that stays in
    ``core.runner_failover``; callers wanting it inject a custom predicate.
    """
    if error is not None:
        return isinstance(error, RunnerBackendError)
    if result is not None:
        return result.status in (
            AgentResultStatus.FAILED,
            AgentResultStatus.TIMED_OUT,
        )
    return False


# --- Executor ----------------------------------------------------------------


class FailoverExecutor:
    """Runs a ``FailoverPlan`` against a ``RunnerRegistry``, recording hops.

    Constructor injection only: the default registry constructs no backends
    (its factories are lazy) and the default eligibility is
    ``default_failover_eligibility``.
    """

    def __init__(
        self,
        registry: RunnerRegistry | None = None,
        *,
        eligibility: FailoverEligibility | None = None,
    ) -> None:
        self._registry = registry if registry is not None else RunnerRegistry()
        self._eligibility: FailoverEligibility = (
            eligibility if eligibility is not None else default_failover_eligibility
        )

    def execute(
        self, invocation: AgentInvocation, plan: FailoverPlan
    ) -> AgentResult:
        """Execute ``plan`` for ``invocation``, returning the first success.

        Typed-only contract (§10): non-``AgentInvocation`` / non-``FailoverPlan``
        arguments raise ``FailoverConfigurationError`` rather than letting an
        incidental ``AttributeError``/``TypeError`` escape the public API.
        """
        if not isinstance(invocation, AgentInvocation):
            raise FailoverConfigurationError(
                f"invocation: must be an AgentInvocation, got {type(invocation).__name__}"
            )
        if not isinstance(plan, FailoverPlan):
            raise FailoverConfigurationError(
                f"plan: must be a FailoverPlan, got {type(plan).__name__}"
            )

        entries = plan.entries
        last_index = len(entries) - 1
        attempts: list[FailoverMetadata] = []

        for i, route in enumerate(entries):
            attempt_model = route.model or invocation.model
            # Fresh invocation: change only runner/model; every other field
            # preserved; the original invocation is never mutated. `replace`
            # re-runs `AgentInvocation.__post_init__`, which re-copies
            # `env_overrides`/`metadata`.
            attempt = replace(invocation, runner=route.runner, model=attempt_model)

            result: AgentResult | None = None
            error: RunnerBackendError | None = None
            try:
                backend = self._registry.resolve(route.runner)
                result = backend.invoke(attempt)
            except (KeyboardInterrupt, SystemExit):
                raise
            except RunnerBackendError as exc:
                error = exc
            # Any non-RunnerBackendError propagates unwrapped (no swallowing).

            if error is None and result.status == AgentResultStatus.SUCCEEDED:
                return self._attach_failover_metadata(result, attempts)

            eligible = self._eligibility(
                invocation=invocation,
                result=result,
                error=error,
                attempt_index=i,
            )
            if not eligible:
                # Non-failover path (§10): honor the raw outcome, never mislabel
                # as exhaustion.
                if error is not None:
                    raise error
                return self._attach_failover_metadata(result, attempts)

            if i == last_index:
                raise self._exhausted(invocation, route, attempt_model, result, error, attempts)

            next_route = entries[i + 1]
            attempts.append(
                FailoverMetadata(
                    attempt_index=i,
                    from_runner=route.runner,
                    from_model=attempt_model,
                    to_runner=next_route.runner,
                    to_model=next_route.model or invocation.model,
                    reason=self._reason(result, error),
                )
            )

        # Unreachable: a nonempty plan either returns or raises above.
        raise FailoverConfigurationError("entries: failover route produced no attempts")

    @staticmethod
    def _reason(
        result: AgentResult | None, error: RunnerBackendError | None
    ) -> str:
        """A leak-free typed token for a hop (§8/§9).

        Raised error -> ``type(error).__name__``. Returned failure ->
        ``status.value`` plus ``":" + error_type`` when present. Free-form
        message/stderr/response text is deliberately excluded.
        """
        if error is not None:
            return type(error).__name__
        assert result is not None  # exactly one of result/error is set here
        token = result.status.value
        if result.error_type:
            token = f"{token}:{result.error_type}"
        return token

    @staticmethod
    def _exhausted(
        invocation: AgentInvocation,
        route: RunnerRoute,
        attempt_model: str | None,
        result: AgentResult | None,
        error: RunnerBackendError | None,
        attempts: list[FailoverMetadata],
    ) -> FailoverExhaustedError:
        total = len(attempts) + 1
        message = (
            f"failover exhausted after {total} attempt(s); "
            f"final runner {route.runner!r}"
        )
        exc = FailoverExhaustedError(
            message,
            failover_attempts=tuple(attempts),
            final_result=result,
            agent=invocation.agent,
            runner=route.runner,
            model=attempt_model,
        )
        if error is not None:
            exc.__cause__ = error
        return exc

    @staticmethod
    def _attach_failover_metadata(
        result: AgentResult, attempts: list[FailoverMetadata]
    ) -> AgentResult:
        if not attempts:
            # No hops recorded: return unchanged, preserving any
            # backend-provided `failover_attempts` (§6).
            return result
        if result.failover_attempts is None:
            return replace(result, failover_attempts=tuple(attempts))
        # Backend already recorded its own hops while we also recorded some:
        # ambiguous composition — refuse to silently overwrite (§6).
        raise FailoverMetadataConflictError(
            "backend returned its own failover_attempts while the runner layer "
            "also recorded failover hops",
            agent=result.agent,
            runner=result.runner,
            model=result.model,
        )
