"""Runner contracts (Prompt 12).

Typed, runner-neutral data contracts for a single agent execution and its
result. Stdlib-only leaf package — imports nothing from `core`, `workflow`,
`server`, `telemetry`, `opik`, or vendor SDKs. Not yet wired into production
dispatch; a later prompt places these behind a `RunnerBackend`.
"""

from __future__ import annotations

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
    "TraceContext",
    "from_completed_process",
]
