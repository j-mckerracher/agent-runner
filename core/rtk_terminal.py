"""Framework-agnostic helper for invoking RTK on shell commands.

RTK (`rtk`) compresses noisy shell output (e.g. `cat`, `grep`, `ls`, `tree`)
into a token-cheap representation. This module provides a thin wrapper that:

- Detects whether the `rtk` CLI is available on PATH.
- Asks RTK to rewrite a command via `rtk rewrite`.
- Runs the resulting command (or the original, when RTK is missing) via
  `subprocess.run`.

It is intentionally framework-agnostic: no OpenHands, LangChain, or other
third-party tool surface. Both the workflow runner and any future caller can
use it directly.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

RewriteStatus = Literal[
    "rewritten",
    "already_rtk",
    "no_match",
    "rtk_missing",
    "raw_requested",
    "rewrite_failed",
]


@dataclass
class TerminalResult:
    original_command: str
    executed_command: str
    used_rtk: bool
    rewrite_status: RewriteStatus
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def rtk_available() -> bool:
    """Return True when the `rtk` binary is on PATH."""
    return shutil.which("rtk") is not None


def rewrite_command(command: str, *, timeout: float = 10.0) -> str | None:
    """Ask RTK to rewrite *command*; return the rewritten form or None.

    Returns None when RTK is missing, when the rewrite call fails, or when
    RTK produces no rewrite for this command.
    """
    if not rtk_available():
        return None
    try:
        result = subprocess.run(
            ["rtk", "rewrite", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("rewrite_command: subprocess error: %s", exc)
        return None
    if result.returncode != 0:
        return None
    rewritten = (result.stdout or "").strip()
    return rewritten or None


def run_terminal(
    command: str | list[str],
    *,
    mode: Literal["auto", "rtk", "raw"] = "auto",
    timeout: float | None = None,
    cwd: str | None = None,
    env: dict | None = None,
) -> TerminalResult:
    """Execute *command*, optionally rewritten by RTK.

    mode:
      - auto: rewrite via RTK when available; fall back to raw otherwise.
      - rtk:  rewrite via RTK; fail with returncode != 0 when RTK is missing or
              produces no rewrite.
      - raw:  always execute the original command without RTK.
    """
    if isinstance(command, list):
        original = " ".join(shlex.quote(part) for part in command)
    else:
        original = (command or "").strip()

    if not original:
        return TerminalResult(
            original_command=original,
            executed_command="",
            used_rtk=False,
            rewrite_status="rewrite_failed",
            returncode=2,
            stdout="",
            stderr="empty command",
        )

    if mode == "raw":
        return _run_shell(original, original, used_rtk=False,
                          status="raw_requested", timeout=timeout, cwd=cwd, env=env)

    if original.startswith("rtk "):
        return _run_shell(original, original, used_rtk=True,
                          status="already_rtk", timeout=timeout, cwd=cwd, env=env)

    if not rtk_available():
        status: RewriteStatus = "rtk_missing"
        if mode == "rtk":
            return TerminalResult(
                original_command=original,
                executed_command=original,
                used_rtk=False,
                rewrite_status=status,
                returncode=127,
                stdout="",
                stderr="rtk is not installed",
            )
        return _run_shell(original, original, used_rtk=False,
                          status=status, timeout=timeout, cwd=cwd, env=env)

    rewritten = rewrite_command(original)
    if rewritten is None:
        if mode == "rtk":
            return TerminalResult(
                original_command=original,
                executed_command=original,
                used_rtk=False,
                rewrite_status="no_match",
                returncode=3,
                stdout="",
                stderr="rtk produced no rewrite",
            )
        return _run_shell(original, original, used_rtk=False,
                          status="no_match", timeout=timeout, cwd=cwd, env=env)

    status = "already_rtk" if rewritten == original else "rewritten"
    return _run_shell(original, rewritten, used_rtk=True,
                      status=status, timeout=timeout, cwd=cwd, env=env)


def _run_shell(
    original: str,
    executed: str,
    *,
    used_rtk: bool,
    status: RewriteStatus,
    timeout: float | None,
    cwd: str | None,
    env: dict | None,
) -> TerminalResult:
    try:
        result = subprocess.run(
            executed,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=False,
        )
        return TerminalResult(
            original_command=original,
            executed_command=executed,
            used_rtk=used_rtk,
            rewrite_status=status,
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return TerminalResult(
            original_command=original,
            executed_command=executed,
            used_rtk=used_rtk,
            rewrite_status=status,
            returncode=124,
            stdout=(exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=(exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            timed_out=True,
        )
