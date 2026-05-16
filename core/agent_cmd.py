"""Public LLM-invocation surface used by eval/* and other external callers.

Delegates to the CLI-subprocess runners in core.run_cmds. The public symbols
(run_agent_cmd, is_transient_runner_failure_text) are preserved so legacy
callers (eval/synthesize.py, eval/validate_calibration.py) keep working.
"""

from __future__ import annotations

from .run_cmds import is_transient_runner_failure_text, run_agent_cmd

__all__ = ["is_transient_runner_failure_text", "run_agent_cmd"]
