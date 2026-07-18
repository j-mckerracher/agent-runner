"""Prompt 8 — WorkflowRunner Shell.

Explicit, typed boundary around the existing `run.py::main` orchestration:

    RunSpec -> WorkflowRunner -> run.py::main -> WorkflowResult

This package only imports stdlib and `telemetry` at import time — no
`run`, `server`, or `opik` import happens until a `WorkflowRunner` with
the default adapter is actually invoked. See `docs/workflow-runner.md`.
"""

from .models import FailureDetail, RunContext, RunSpec, RunStatus, WorkflowResult
from .runner import LegacyWorkflowCallable, WorkflowRunner

__all__ = [
    "RunSpec",
    "RunContext",
    "WorkflowResult",
    "WorkflowRunner",
    "RunStatus",
    "FailureDetail",
    "LegacyWorkflowCallable",
]
