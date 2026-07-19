"""Runner contracts and execution boundary (Prompts 12–13).

Typed, runner-neutral data contracts for a single agent execution and its
result (Prompt 12), plus the `RunnerBackend` execution seam (Prompt 13).
Stdlib-only leaf package at the root — importing `runners` loads nothing from
`core`, `workflow`, `server`, `eval`, `telemetry`, `opik`, or vendor SDKs.

`LegacyDispatchBackend` is deliberately **not** re-exported here: its public
path is `from runners.legacy import LegacyDispatchBackend`. This keeps
`from runners import *` from pulling in the legacy execution stack.
"""

from __future__ import annotations

from runners.base import (
    RunnerBackend,
    RunnerBackendError,
    RunnerInvocationError,
    UnsupportedInvocationError,
)
from runners.compat import from_completed_process
from runners.models import (
    AgentContractError,
    AgentInvocation,
    AgentResult,
    AgentResultStatus,
    FailoverMetadata,
    MetadataSerializationError,
    RetryMetadata,
    TraceContext,
)

__all__ = [
    "AgentContractError",
    "AgentInvocation",
    "AgentResult",
    "AgentResultStatus",
    "FailoverMetadata",
    "MetadataSerializationError",
    "RetryMetadata",
    "RunnerBackend",
    "RunnerBackendError",
    "RunnerInvocationError",
    "TraceContext",
    "UnsupportedInvocationError",
    "from_completed_process",
]
