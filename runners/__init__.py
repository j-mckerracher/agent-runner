"""Runner contracts and execution boundary (Prompts 12–14).

Typed, runner-neutral data contracts for a single agent execution and its
result (Prompt 12), the `RunnerBackend` execution seam (Prompt 13), and the
concrete per-family adapters (Prompt 14: `ClaudeBackend`, `CodexBackend`,
`GeminiBackend`, `CopilotBackend`, `BuiltinOpenAICompatBackend`,
`OpenAICompatAliasBackend`). Stdlib-only leaf package at the root — importing
`runners` loads nothing from `core`, `workflow`, `server`, `eval`,
`telemetry`, `opik`, or vendor SDKs: every adapter lazy-imports its backend
callable inside `invoke`.

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
from runners.claude import ClaudeBackend
from runners.codex import CodexBackend
from runners.compat import from_completed_process
from runners.copilot import CopilotBackend
from runners.failover import (
    FailoverConfigurationError,
    FailoverExecutor,
    FailoverExhaustedError,
    FailoverMetadataConflictError,
    FailoverPlan,
    RunnerRoute,
    default_failover_eligibility,
)
from runners.gemini import GeminiBackend
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
from runners.omp import BuiltinOpenAICompatBackend
from runners.openai_compat import OpenAICompatAliasBackend
from runners.registry import (
    InvalidRunnerNameError,
    RegistryConfigurationError,
    RunnerRegistry,
    RunnerSelectionError,
    UnknownRunnerError,
    resolve_backend,
)

__all__ = [
    "AgentContractError",
    "AgentInvocation",
    "AgentResult",
    "AgentResultStatus",
    "BuiltinOpenAICompatBackend",
    "ClaudeBackend",
    "CodexBackend",
    "CopilotBackend",
    "FailoverConfigurationError",
    "FailoverExecutor",
    "FailoverExhaustedError",
    "FailoverMetadata",
    "FailoverMetadataConflictError",
    "FailoverPlan",
    "GeminiBackend",
    "InvalidRunnerNameError",
    "MetadataSerializationError",
    "OpenAICompatAliasBackend",
    "RegistryConfigurationError",
    "RetryMetadata",
    "RunnerBackend",
    "RunnerBackendError",
    "RunnerInvocationError",
    "RunnerRegistry",
    "RunnerRoute",
    "RunnerSelectionError",
    "TraceContext",
    "UnknownRunnerError",
    "UnsupportedInvocationError",
    "default_failover_eligibility",
    "from_completed_process",
    "resolve_backend",
]
