"""Prompt 12 — Agent Invocation & Result Contracts: typed data models.

Runner-neutral, stdlib-only dataclasses describing *one agent execution*
(`AgentInvocation`) and *its result* (`AgentResult`), plus small supporting
types (`AgentResultStatus`, `TraceContext`, `RetryMetadata`,
`FailoverMetadata`).

These are pure data contracts. This module imports nothing from `core`,
`workflow`, `server`, `telemetry`, `opik`, or any vendor SDK — only the
standard library — so it carries zero optional-dependency or circular-import
risk. A later prompt will place these types behind a `RunnerBackend`
abstraction; production dispatch (`core.agent_cmd.run_agent_cmd`) is
unchanged and keeps its exact `-> str` signature. The contracts are allowed
to be unused by production for now.

Serialization rules (mirroring `telemetry/events.py:253-285`):

- `None` means "missing/unknown" and is *omitted* from `to_dict()`. An
  observed `0` / `0.0` is a real measurement and is kept.
- `to_json()` produces a deterministic, sorted-keys line.
- `from_dict()` ignores unknown top-level keys (forward-compat — custom data
  belongs in `metadata`).
- `metadata` (and, where present, `env_overrides`) JSON-ability is guarded so
  a serialization failure raises a clear error *before* any partial output is
  produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class AgentContractError(ValueError):
    """Raised when an `AgentInvocation`/`AgentResult` fails validation.

    Carries every problem found (not just the first) so callers see the full
    set of offending fields in one pass, mirroring
    `telemetry.events.TraceValidationError`.
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid agent contract")


class MetadataSerializationError(ValueError):
    """Raised when `metadata`/`env_overrides` holds a value `json` cannot encode.

    Kept independent from `telemetry.events.MetadataSerializationError` so this
    leaf package imports nothing outside the standard library.
    """


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _ensure_jsonable(value: Any, *, where: str) -> None:
    """Recursively verify `value` is made only of JSON-compatible types.

    Never stringifies unsupported values as a fallback — raises
    `MetadataSerializationError` naming the offending location instead.
    """
    if isinstance(value, _JSON_SCALAR_TYPES):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MetadataSerializationError(
                    f"{where}: dict keys must be strings, got {type(key).__name__}"
                )
            _ensure_jsonable(item, where=f"{where}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _ensure_jsonable(item, where=f"{where}[{index}]")
        return
    raise MetadataSerializationError(
        f"{where}: value of type {type(value).__name__} is not JSON-serializable"
    )


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


def _coerce_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    return value if isinstance(value, Path) else Path(value)


class AgentResultStatus(str, Enum):
    """Terminal status of a single agent execution.

    Exactly three states — no speculative vocabulary. `TIMED_OUT` is distinct
    from `FAILED` so a timeout is never confused with a runner-reported error.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class TraceContext:
    """Lightweight trace reference for one agent execution.

    Deliberately has **no** `trace_id`: the invocation-level `run_id` is the
    sole run identifier, avoiding two conflicting run ids. `span_id` /
    `parent_span_id` mirror the terminology already used by
    `telemetry.events.TraceEvent`.
    """

    span_id: str | None = None
    parent_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.span_id is not None:
            result["span_id"] = self.span_id
        if self.parent_span_id is not None:
            result["parent_span_id"] = self.parent_span_id
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceContext":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class RetryMetadata:
    """Data-only record of retry activity for one execution — no policy.

    `attempt_count >= 1` always holds because at least one initial invocation
    occurred. `max_attempts`, when supplied, must be `>= 1`.
    """

    attempt_count: int
    max_attempts: int | None = None
    last_error_type: str | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.attempt_count, int) or isinstance(self.attempt_count, bool):
            errors.append(f"attempt_count: must be an int, got {self.attempt_count!r}")
        elif self.attempt_count < 1:
            errors.append(f"attempt_count: must be >= 1, got {self.attempt_count!r}")
        if self.max_attempts is not None:
            if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
                errors.append(f"max_attempts: must be an int or None, got {self.max_attempts!r}")
            elif self.max_attempts < 1:
                errors.append(f"max_attempts: must be >= 1 when supplied, got {self.max_attempts!r}")
        if self.last_error_type is not None and not _nonempty_str(self.last_error_type):
            errors.append(
                f"last_error_type: must be a nonempty string or None, got {self.last_error_type!r}"
            )
        if errors:
            raise AgentContractError(errors)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"attempt_count": self.attempt_count}
        if self.max_attempts is not None:
            result["max_attempts"] = self.max_attempts
        if self.last_error_type is not None:
            result["last_error_type"] = self.last_error_type
        if self.retryable is not None:
            result["retryable"] = self.retryable
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RetryMetadata":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class FailoverMetadata:
    """Data-only record of one failover attempt/hop — no policy.

    Mirrors one `core.run_cmds.RunnerFailoverPolicy._exhausted` entry. Ordered
    multi-hop failover is represented by the *tuple* on
    `AgentResult.failover_attempts`, preserving order and intermediate
    failures.
    """

    attempt_index: int
    from_runner: str
    to_runner: str | None = None
    reason: str | None = None
    from_model: str | None = None
    to_model: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.attempt_index, int) or isinstance(self.attempt_index, bool):
            errors.append(f"attempt_index: must be an int, got {self.attempt_index!r}")
        elif self.attempt_index < 0:
            errors.append(f"attempt_index: must be >= 0, got {self.attempt_index!r}")
        if not _nonempty_str(self.from_runner):
            errors.append(f"from_runner: must be a nonempty string, got {self.from_runner!r}")
        for name in ("to_runner", "reason", "from_model", "to_model"):
            value = getattr(self, name)
            if value is not None and not _nonempty_str(value):
                errors.append(f"{name}: must be a nonempty string or None, got {value!r}")
        if errors:
            raise AgentContractError(errors)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "attempt_index": self.attempt_index,
            "from_runner": self.from_runner,
        }
        for name in ("to_runner", "reason", "from_model", "to_model"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FailoverMetadata":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class AgentInvocation:
    """Immutable description of a single requested agent execution.

    Required identity: `agent`, `runner` (validated nonempty). `model` is
    optional — the production boundary accepts `runner_model=None` and the
    concrete model may be resolved later in dispatch — but is validated
    nonempty when supplied. Custom/unknown runner names stay representable;
    this contract is not a registry.

    At least one prompt source (`prompt` or `prompt_ref`) must be present.

    Construction is pure: path fields are coerced `str -> Path` without
    touching the filesystem, and no runner call, config read, or filesystem
    access happens. `env_overrides` and `metadata` are defensively copied and
    `allowed_tools` is normalized to a tuple so a frozen instance is truly
    immutable against later caller mutation.
    """

    agent: str
    runner: str
    model: str | None = None
    prompt: str | None = None
    prompt_ref: str | None = None
    repo: Path | None = None
    working_dir: Path | None = None
    timeout_s: float | None = None
    env_overrides: Mapping[str, str] | None = None
    allowed_tools: tuple[str, ...] | None = None
    trace_context: TraceContext | None = None
    change_id: str | None = None
    run_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Pure normalization first (no filesystem touch).
        object.__setattr__(self, "repo", _coerce_path(self.repo))
        object.__setattr__(self, "working_dir", _coerce_path(self.working_dir))
        if self.env_overrides is not None:
            object.__setattr__(self, "env_overrides", dict(self.env_overrides))
        if self.allowed_tools is not None:
            object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "metadata", dict(self.metadata))

        errors: list[str] = []
        if not _nonempty_str(self.agent):
            errors.append(f"agent: must be a nonempty string, got {self.agent!r}")
        if not _nonempty_str(self.runner):
            errors.append(f"runner: must be a nonempty string, got {self.runner!r}")
        if self.model is not None and not _nonempty_str(self.model):
            errors.append(f"model: must be a nonempty string or None, got {self.model!r}")
        if not _nonempty_str(self.prompt) and not _nonempty_str(self.prompt_ref):
            errors.append("prompt: at least one of prompt or prompt_ref must be supplied")
        if self.timeout_s is not None:
            if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)):
                errors.append(f"timeout_s: must be a number or None, got {self.timeout_s!r}")
            elif self.timeout_s < 0:
                errors.append(f"timeout_s: must be >= 0, got {self.timeout_s!r}")
        for name in ("change_id", "run_id"):
            value = getattr(self, name)
            if value is not None and not _nonempty_str(value):
                errors.append(f"{name}: must be a nonempty string or None, got {value!r}")
        if not isinstance(self.metadata, dict):
            errors.append(f"metadata: must be a mapping, got {type(self.metadata).__name__}")
        if errors:
            raise AgentContractError(errors)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agent": self.agent,
            "runner": self.runner,
        }
        if self.model is not None:
            result["model"] = self.model
        if self.prompt is not None:
            result["prompt"] = self.prompt
        if self.prompt_ref is not None:
            result["prompt_ref"] = self.prompt_ref
        if self.repo is not None:
            result["repo"] = str(self.repo)
        if self.working_dir is not None:
            result["working_dir"] = str(self.working_dir)
        if self.timeout_s is not None:
            result["timeout_s"] = self.timeout_s
        if self.env_overrides is not None:
            _ensure_jsonable(self.env_overrides, where="AgentInvocation.env_overrides")
            result["env_overrides"] = dict(self.env_overrides)
        if self.allowed_tools is not None:
            result["allowed_tools"] = list(self.allowed_tools)
        if self.trace_context is not None:
            result["trace_context"] = self.trace_context.to_dict()
        if self.change_id is not None:
            result["change_id"] = self.change_id
        if self.run_id is not None:
            result["run_id"] = self.run_id
        if self.metadata:
            _ensure_jsonable(self.metadata, where="AgentInvocation.metadata")
            result["metadata"] = dict(self.metadata)
        return result

    def to_json(self) -> str:
        """Serialize to a deterministic, sorted-keys JSON line."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentInvocation":
        """Reconstruct from a plain dict; unknown top-level keys are ignored."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "trace_context" in kwargs and isinstance(kwargs["trace_context"], Mapping):
            kwargs["trace_context"] = TraceContext.from_dict(kwargs["trace_context"])
        if "allowed_tools" in kwargs and kwargs["allowed_tools"] is not None:
            kwargs["allowed_tools"] = tuple(kwargs["allowed_tools"])
        return cls(**kwargs)


@dataclass
class AgentResult:
    """Structured result of a single agent execution.

    Mutable — matching `workflow.models.WorkflowResult` — because callers
    assemble it incrementally as telemetry arrives. `status` is the only
    required field. Identity (`agent`/`runner`/`model`) is caller-supplied and
    optional.

    `stdout` and `stderr` are kept separate. Telemetry fields default to
    `None` (missing) and are omitted from serialization; an observed `0` is a
    real measurement and is kept. Nothing here parses runner output into
    invented metrics, and success is not forced to have nonempty output.
    """

    status: AgentResultStatus
    agent: str | None = None
    runner: str | None = None
    model: str | None = None
    response_text: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    session_log_ref: str | None = None
    duration_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    retry: RetryMetadata | None = None
    failover_attempts: tuple[FailoverMetadata, ...] | None = None
    artifacts_touched: tuple[str, ...] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentResultStatus):
            self.status = AgentResultStatus(self.status)
        if self.failover_attempts is not None:
            self.failover_attempts = tuple(self.failover_attempts)
        if self.artifacts_touched is not None:
            self.artifacts_touched = tuple(self.artifacts_touched)
        # Defensive copy so later caller mutation cannot alter this instance.
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status.value}
        for name in (
            "agent",
            "runner",
            "model",
            "response_text",
            "stdout",
            "stderr",
            "exit_code",
            "session_log_ref",
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "error_type",
            "error_message",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        if self.retry is not None:
            result["retry"] = self.retry.to_dict()
        if self.failover_attempts is not None:
            result["failover_attempts"] = [hop.to_dict() for hop in self.failover_attempts]
        if self.artifacts_touched is not None:
            result["artifacts_touched"] = list(self.artifacts_touched)
        if self.metadata:
            _ensure_jsonable(self.metadata, where="AgentResult.metadata")
            result["metadata"] = dict(self.metadata)
        return result

    def to_json(self) -> str:
        """Serialize to a deterministic, sorted-keys JSON line."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentResult":
        """Reconstruct from a plain dict; unknown top-level keys are ignored."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        if "status" in kwargs:
            kwargs["status"] = AgentResultStatus(kwargs["status"])
        if kwargs.get("retry") is not None and isinstance(kwargs["retry"], Mapping):
            kwargs["retry"] = RetryMetadata.from_dict(kwargs["retry"])
        if kwargs.get("failover_attempts") is not None:
            kwargs["failover_attempts"] = tuple(
                hop if isinstance(hop, FailoverMetadata) else FailoverMetadata.from_dict(hop)
                for hop in kwargs["failover_attempts"]
            )
        if kwargs.get("artifacts_touched") is not None:
            kwargs["artifacts_touched"] = tuple(kwargs["artifacts_touched"])
        return cls(**kwargs)
