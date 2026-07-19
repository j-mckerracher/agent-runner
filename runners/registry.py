"""Prompt 15 — centralized runner backend registry (selection boundary).

`RunnerRegistry` maps a runner name to exactly one `RunnerBackend`, replacing
scattered ``if/elif`` runner-name dispatch with one explicit, testable
operation. This module is the *selection* boundary only — it does not migrate
any production call site.

Leaf-safe: this module imports only ``runners.*`` + stdlib ``typing``. Backend
adapters lazy-import their ``core.run_cmds`` callable inside ``invoke``, so
neither importing this module, constructing a registry, nor resolving a backend
loads anything from ``core``, ``workflow``, ``server``, ``eval``,
``telemetry``, ``opik``, or a vendor SDK.

Selection precedence mirrors ``core.run_cmds._dispatch_agent_cmd`` faithfully:

1. copilot family (``is_copilot_runner``, prefix-only): ``copilot`` or any
   ``copilot-*`` (bare ``copilot-`` is accepted, matching production) resolves
   to ``CopilotBackend``. The original alias is preserved — the registry does
   not collapse it to canonical ``copilot``.
2. exact built-in (or injected custom factory key).
3. injected openai-compat alias predicate (inject-to-enable; a bare
   ``RunnerRegistry()`` resolves only built-ins).
4. otherwise `UnknownRunnerError`.

Built-ins are matched before the alias predicate is ever consulted, so a
built-in wins even if the predicate would accept the name.
"""

from __future__ import annotations

from typing import Callable, Mapping

from runners.base import RunnerBackend, RunnerBackendError
from runners.claude import ClaudeBackend
from runners.codex import CodexBackend
from runners.copilot import CopilotBackend
from runners.gemini import GeminiBackend
from runners.omp import BuiltinOpenAICompatBackend
from runners.openai_compat import OpenAICompatAliasBackend

# Built-in runner name set. Hardcoded here (deliberately duplicated from
# `core.runner_models.KNOWN_RUNNERS`) so this leaf module never imports `core`.
BUILTIN_RUNNERS = ("claude", "codex", "gemini", "copilot", "openai-compat")


class RunnerSelectionError(RunnerBackendError):
    """Base error for the registry selection boundary.

    Subclass of `RunnerBackendError`, so it carries the safe invocation
    identity (`agent`/`runner`/`model`) and never any prompt, metadata,
    environment, or credential payload.
    """


class UnknownRunnerError(RunnerSelectionError):
    """No backend is registered for the requested runner name.

    Carries `.requested` (original), `.normalized` (lowercased), and
    `.supported` (the built-in tuple). The message names the requested name and
    the supported built-ins and must not imply arbitrary aliases are supported.
    """

    def __init__(
        self,
        requested: str,
        normalized: str,
        supported: tuple[str, ...],
    ) -> None:
        self.requested = requested
        self.normalized = normalized
        self.supported = tuple(supported)
        super().__init__(
            f"unknown runner {requested!r}; supported built-in runners: "
            f"{', '.join(self.supported)}",
            runner=requested,
        )


class InvalidRunnerNameError(RunnerSelectionError):
    """The requested runner name is not a usable string.

    Raised for non-`str`/`None` names and for empty/whitespace-only names.
    Accepts a non-`str`/`None` `requested` for diagnostics; the parent
    `runner=` field is guarded to `str | None`.
    """

    def __init__(self, requested: object, message: str) -> None:
        self.requested = requested
        super().__init__(
            message,
            runner=requested if isinstance(requested, str) else None,
        )


class RegistryConfigurationError(RunnerSelectionError):
    """The registry configuration or a factory produced an unusable result.

    Covers: non-`Mapping` factories, non-string/empty factory keys, non-callable
    factory values, a non-callable alias predicate, a factory that raises, a
    factory returning a non-`RunnerBackend`, and an alias predicate that raises.
    Never a raw `KeyError` or incidental `TypeError`.
    """


# Default factories: each adapter class is a valid zero-arg
# `Callable[[], RunnerBackend]` because its constructor defaults `backend=None`.
_DEFAULT_FACTORIES: dict[str, Callable[[], RunnerBackend]] = {
    "claude": ClaudeBackend,
    "codex": CodexBackend,
    "gemini": GeminiBackend,
    "copilot": CopilotBackend,
    "openai-compat": BuiltinOpenAICompatBackend,
}


def _normalize(runner: object) -> str:
    """Validate + normalize a requested runner name.

    Rejects non-`str`/`None` and empty/whitespace-only names with
    `InvalidRunnerNameError`. Returns ``runner.lower()`` (case-insensitive
    matching, mirroring dispatch's ``runner.lower()``); does *not* strip — a
    leading/trailing-space name stays unknown, matching dispatch.
    """
    if not isinstance(runner, str):
        raise InvalidRunnerNameError(
            runner, f"runner name must be a string, got {type(runner).__name__}"
        )
    if not runner.strip():
        raise InvalidRunnerNameError(
            runner, f"runner name must be a nonempty string, got {runner!r}"
        )
    return runner.lower()


class RunnerRegistry:
    """Maps a runner name to exactly one `RunnerBackend`.

    Factory model (override, not replace): custom factories *override* the
    matching built-in and add new resolvable keys, but never delete the five
    built-ins — a single test override thus cannot orphan ``copilot`` etc. The
    configuration is validated at construction; no backend is instantiated at
    construction or import (factories are called lazily inside `resolve`).
    """

    def __init__(
        self,
        factories: Mapping[str, Callable[[], RunnerBackend]] | None = None,
        supports_openai_compat_alias: Callable[[str], bool] | None = None,
    ) -> None:
        resolved: dict[str, Callable[[], RunnerBackend]] = dict(_DEFAULT_FACTORIES)
        if factories is not None:
            if not isinstance(factories, Mapping):
                raise RegistryConfigurationError("factories must be a mapping or None")
            for key, value in factories.items():
                if not isinstance(key, str) or not key.strip():
                    raise RegistryConfigurationError(
                        "factory keys must be non-empty strings"
                    )
                if not callable(value):
                    raise RegistryConfigurationError(
                        f"factory for {key!r} is not callable"
                    )
            # Keys stored lowercased to match case-insensitive resolution.
            resolved.update({k.lower(): v for k, v in factories.items()})
        if supports_openai_compat_alias is not None and not callable(
            supports_openai_compat_alias
        ):
            raise RegistryConfigurationError(
                "supports_openai_compat_alias must be callable or None"
            )
        self._factories = resolved
        self._supports_alias = supports_openai_compat_alias  # None -> aliases off

    def resolve(self, runner: str) -> RunnerBackend:
        """Resolve `runner` to a fresh `RunnerBackend` (no caching).

        Precedence: copilot family -> exact built-in / injected key -> injected
        alias predicate -> `UnknownRunnerError`. Raises `InvalidRunnerNameError`
        for a bad name and `RegistryConfigurationError` for a bad factory /
        predicate.
        """
        normalized = _normalize(runner)

        # 1. Copilot family FIRST (prefix-only, faithful to is_copilot_runner).
        if normalized == "copilot" or normalized.startswith("copilot-"):
            return self._construct(runner, self._factories.get("copilot"))

        # 2. Other exact built-ins (plus any injected custom keys).
        if normalized in self._factories:
            return self._construct(runner, self._factories.get(normalized))

        # 3. openai-compat alias (inject-to-enable; original name re-validated
        #    by OpenAICompatAliasBackend at invoke time).
        if self._supports_alias is not None and self._call_predicate(runner):
            return OpenAICompatAliasBackend(supports_runner=self._supports_alias)

        # 4. Unknown.
        raise UnknownRunnerError(runner, normalized, BUILTIN_RUNNERS)

    def _call_predicate(self, runner: str) -> bool:
        try:
            return bool(self._supports_alias(runner))
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise RegistryConfigurationError(
                "openai-compat alias predicate failed", runner=runner
            ) from exc

    def _construct(
        self, runner: str, factory: Callable[[], RunnerBackend] | None
    ) -> RunnerBackend:
        # Defensive: the five built-ins can never be deleted, so a selected
        # factory is always present via the public constructor; still guard
        # against a `None` rather than let a raw KeyError escape.
        if factory is None:
            raise RegistryConfigurationError(
                f"no factory registered for runner {runner!r}", runner=runner
            )
        try:
            backend = factory()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise RegistryConfigurationError(
                f"factory for runner {runner!r} failed", runner=runner
            ) from exc
        if not isinstance(backend, RunnerBackend):
            raise RegistryConfigurationError(
                f"factory for runner {runner!r} returned "
                f"{type(backend).__name__}, not a RunnerBackend",
                runner=runner,
            )
        return backend


def resolve_backend(
    runner: str,
    *,
    supports_openai_compat_alias: Callable[[str], bool] | None = None,
) -> RunnerBackend:
    """Stateless convenience: resolve one runner via a fresh `RunnerRegistry`.

    The class stays the testable core; this wraps a one-shot resolution.
    """
    return RunnerRegistry(
        supports_openai_compat_alias=supports_openai_compat_alias
    ).resolve(runner)
