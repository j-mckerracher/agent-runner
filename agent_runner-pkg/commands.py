from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .console import log
from .models import CommandResult


def _coerce_stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    heartbeat_interval: int = 10,
    early_exit_paths: list[Path] | None = None,
) -> CommandResult:
    """Run a subprocess and capture stdout/stderr with heartbeat logging."""

    display_cmd = " ".join(command[:6]) + (" ..." if len(command) > 6 else "")
    log("INFO", f"$ {display_cmd}")
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        log("ERROR", f"Failed to start process: {exc}")
        return CommandResult(command=command, exit_code=1, stdout="", stderr=str(exc))

    last_heartbeat = t0
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=heartbeat_interval)
                break
            except subprocess.TimeoutExpired as exc:
                elapsed = time.monotonic() - t0
                if elapsed >= timeout_seconds:
                    proc.kill()
                    stdout_partial, stderr_partial = proc.communicate()
                    stdout_text = _coerce_stream_text(exc.output) + stdout_partial
                    stderr_text = _coerce_stream_text(exc.stderr) + stderr_partial
                    timeout_message = (
                        f"Command timed out after {timeout_seconds} seconds."
                    )
                    combined_stderr = "\n".join(
                        part for part in [stderr_text.strip(), timeout_message] if part
                    ).strip()
                    log(
                        "ERROR",
                        f"timeout after {timeout_seconds}s  elapsed={elapsed:.1f}s  cmd={display_cmd}",
                    )
                    return CommandResult(
                        command=command,
                        exit_code=124,
                        stdout=stdout_text,
                        stderr=combined_stderr,
                    )
                if early_exit_paths:
                    for path in early_exit_paths:
                        if path.is_file():
                            log(
                                "OK",
                                f"Early exit: artifact found at {path}  elapsed={elapsed:.0f}s — killing process",
                            )
                            proc.kill()
                            stdout_partial, _ = proc.communicate()
                            stdout_text = _coerce_stream_text(exc.output) + stdout_partial
                            return CommandResult(
                                command=command,
                                exit_code=0,
                                stdout=stdout_text,
                                stderr="",
                            )
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    log(
                        "INFO",
                        f"still running…  elapsed={elapsed:.0f}s  cmd={display_cmd}",
                    )
                    last_heartbeat = now
    except KeyboardInterrupt:
        log("WARN", "Interrupted — killing agent process…")
        proc.kill()
        proc.communicate()
        raise

    elapsed = time.monotonic() - t0
    level = "OK" if proc.returncode == 0 else "ERROR"
    log(level, f"exit={proc.returncode}  elapsed={elapsed:.1f}s  cmd={display_cmd}")
    return CommandResult(
        command=command,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )
