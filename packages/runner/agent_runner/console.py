from __future__ import annotations

import json
import textwrap
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CommandResult

_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_LEVEL_STYLES: dict[str, str] = {
    "INFO": "\033[36m",
    "OK": "\033[32m",
    "WARN": "\033[33m",
    "ERROR": "\033[31m",
    "AGENT": "\033[35m",
    "STAGE": "\033[34m",
    "OUTPUT": "\033[90m",
}

STARTUP_ROBOT_ART = textwrap.dedent(
    r"""
          [::]
        .-:||:-.
       /  _  _  \
      |  (o)(o)  |
      |    __    |
       \  '--'  /
        `-.__.-'
        /|_||_|\
       /_/ /\ \_\
    """
).strip()

_EVENT_PREFIX = "##EVENT##"


def _ts() -> str:
    """Return a short HH:MM:SS timestamp."""

    return datetime.now().strftime("%H:%M:%S")


def log(level: str, message: str) -> None:
    """Print a timestamped, coloured log line to stdout."""

    colour = _LEVEL_STYLES.get(level, "")
    label = f"{colour}{_ANSI_BOLD}[{level:^5}]{_ANSI_RESET}"
    print(f"{_ANSI_BOLD}{_ts()}{_ANSI_RESET} {label} {message}", flush=True)


def emit_event(event_type: str, **kwargs: object) -> None:
    """Emit a structured event line on stdout for external consumers."""

    payload = {"type": event_type, "ts": _ts(), **kwargs}
    print(f"{_EVENT_PREFIX} {json.dumps(payload, default=str)}", flush=True)


def print_stage_banner(title: str) -> None:
    """Print a prominent visual separator for a workflow stage."""

    width = 72
    bar = "─" * width
    colour = _LEVEL_STYLES["STAGE"]
    print(flush=True)
    print(f"{colour}{_ANSI_BOLD}┌{bar}┐{_ANSI_RESET}", flush=True)
    print(
        f"{colour}{_ANSI_BOLD}│  {title:<{width - 2}}│{_ANSI_RESET}",
        flush=True,
    )
    print(f"{colour}{_ANSI_BOLD}└{bar}┘{_ANSI_RESET}", flush=True)
    print(flush=True)


def _req_sym(command: str) -> str:
    """Return ✓ if *command* is on PATH, ✗ otherwise."""

    import shutil

    return "✓" if shutil.which(command) else "✗"


def print_startup_robot() -> None:
    """Print the interactive launch banner with a concise requirements check."""

    import sys

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    py_sym = "✓" if sys.version_info >= (3, 9) else "✗"
    req = (
        f"  Requires:  Python ≥3.9 {py_sym} {py_ver}"
        f"  │  git {_req_sym('git')}"
        f"  │  AI: copilot {_req_sym('copilot')}  claude {_req_sym('claude')}  (need ≥1)"
        f"  │  az {_req_sym('az')} (optional, ADO fetch)"
    )

    print(STARTUP_ROBOT_ART, flush=True)
    print(flush=True)
    print(req, flush=True)
    print(flush=True)


def print_agent_output(result: CommandResult, agent_key: str) -> None:
    """Print the agent's stdout and stderr to the console."""

    colour = _LEVEL_STYLES["OUTPUT"]
    reset = _ANSI_RESET
    if result.stdout.strip():
        print(
            f"{colour}{'─' * 60}  [{agent_key}] stdout  {'─' * 4}{reset}",
            flush=True,
        )
        for line in result.stdout.rstrip().splitlines():
            print(f"{colour}  {line}{reset}", flush=True)
        print(f"{colour}{'─' * 76}{reset}", flush=True)
    if result.stderr.strip():
        err_colour = _LEVEL_STYLES["WARN"]
        print(
            f"{err_colour}{'─' * 60}  [{agent_key}] stderr  {'─' * 4}{reset}",
            flush=True,
        )
        for line in result.stderr.rstrip().splitlines():
            print(f"{err_colour}  {line}{reset}", flush=True)
        print(f"{err_colour}{'─' * 76}{reset}", flush=True)

