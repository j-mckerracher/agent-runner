"""Prompt 12 — minimal compatibility helper.

`from_completed_process` maps a `subprocess.CompletedProcess` (the shape
today's runners already produce) into an `AgentResult`, without touching
`core.agent_cmd.run_agent_cmd` or invoking any production runner. It is a
pure function reusable by the next `RunnerBackend` prompt.

Fabrication is deliberately avoided: telemetry the process object does not
carry (`duration_ms`, `tokens_in`, `tokens_out`, `cost_usd`) stays `None`.
"""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any

from runners.models import AgentResult, AgentResultStatus


def from_completed_process(
    proc: CompletedProcess[Any],
    *,
    status: AgentResultStatus | None = None,
    agent: str | None = None,
    runner: str | None = None,
    model: str | None = None,
    response_text: str | None = None,
    session_log_ref: str | None = None,
) -> AgentResult:
    """Build an `AgentResult` from a finished `CompletedProcess`.

    - `proc.returncode` → `exit_code`; `proc.stdout` / `proc.stderr` are kept
      separate.
    - `status` defaults to `SUCCEEDED` when `returncode == 0`, else `FAILED`.
      `TIMED_OUT` is never inferred here — a `CompletedProcess` cannot express
      a timeout, so only an explicit `status=` argument can set it.
    - Caller-supplied identity (`agent`/`runner`/`model`) is retained.
    - All telemetry stays `None`: missing data stays missing.
    """
    resolved = status
    if resolved is None:
        resolved = (
            AgentResultStatus.SUCCEEDED if proc.returncode == 0 else AgentResultStatus.FAILED
        )
    return AgentResult(
        status=resolved,
        agent=agent,
        runner=runner,
        model=model,
        response_text=response_text,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        session_log_ref=session_log_ref,
    )
