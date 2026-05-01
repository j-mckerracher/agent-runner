"""Factory and execution helpers for simple built-in checks."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from .models import CheckDefinition, CheckResult, FailureReason


def contains_check(
    id: str,
    label: str,
    subject: str,
    substring: str,
    *,
    difficulty: Optional[str] = None,
    suggested_difficulty: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> CheckDefinition:
    return CheckDefinition(
        id=id,
        label=label,
        mechanism="contains",
        subject=subject,
        expected=substring,
        difficulty=difficulty,
        suggested_difficulty=suggested_difficulty,
        metadata=metadata or {},
    )


def matches_check(
    id: str,
    label: str,
    subject: str,
    pattern: str,
    *,
    difficulty: Optional[str] = None,
    suggested_difficulty: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> CheckDefinition:
    return CheckDefinition(
        id=id,
        label=label,
        mechanism="matches",
        subject=subject,
        expected=pattern,
        difficulty=difficulty,
        suggested_difficulty=suggested_difficulty,
        metadata=metadata or {},
    )


def command_check(
    id: str,
    label: str,
    subject: str,
    cmd: Union[str, Sequence[str]],
    *,
    timeout_seconds: Optional[int] = None,
    difficulty: Optional[str] = None,
    suggested_difficulty: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> CheckDefinition:
    return CheckDefinition(
        id=id,
        label=label,
        mechanism="command",
        subject=subject,
        command=cmd,
        timeout_seconds=timeout_seconds,
        difficulty=difficulty,
        suggested_difficulty=suggested_difficulty,
        metadata=metadata or {},
    )


def run_check(
    definition: CheckDefinition,
    agent_output: str = "",
    repo_path: Optional[Union[str, Path]] = None,
    timeout_seconds: Optional[int] = None,
) -> CheckResult:
    if definition.mechanism == "contains":
        return _run_contains(definition, agent_output)
    if definition.mechanism == "matches":
        return _run_matches(definition, agent_output)
    if definition.mechanism == "command":
        return _run_command(definition, repo_path, timeout_seconds)
    raise ValueError(f"Unsupported check mechanism: {definition.mechanism}")


def _run_contains(definition: CheckDefinition, agent_output: str) -> CheckResult:
    if not agent_output:
        return _result(definition, False, attempted=False, failure_reason="NO_ATTEMPT", message="No agent output")
    passed = definition.expected in agent_output if definition.expected is not None else False
    return _result(
        definition,
        passed,
        failure_reason=None if passed else "ASSERTION_MISS",
        message="substring found" if passed else "substring not found",
    )


def _run_matches(definition: CheckDefinition, agent_output: str) -> CheckResult:
    if not agent_output:
        return _result(definition, False, attempted=False, failure_reason="NO_ATTEMPT", message="No agent output")
    passed = re.search(definition.expected or "", agent_output, flags=re.MULTILINE) is not None
    return _result(
        definition,
        passed,
        failure_reason=None if passed else "ASSERTION_MISS",
        message="pattern matched" if passed else "pattern did not match",
    )


def _run_command(
    definition: CheckDefinition,
    repo_path: Optional[Union[str, Path]],
    timeout_seconds: Optional[int],
) -> CheckResult:
    command = definition.command
    if command is None:
        return _result(definition, False, failure_reason="BUILD_ERROR", message="Missing command")
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        return _result(definition, False, failure_reason="BUILD_ERROR", message="Empty command")
    timeout = timeout_seconds or definition.timeout_seconds or 30
    try:
        completed = subprocess.run(
            argv,
            cwd=str(repo_path) if repo_path else None,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        return _result(definition, False, failure_reason="TIMEOUT", message=f"Command timed out after {exc.timeout}s")
    passed = completed.returncode == 0
    message = completed.stdout.strip() or completed.stderr.strip() or f"exit code {completed.returncode}"
    return _result(definition, passed, failure_reason=None if passed else "BUILD_ERROR", message=message)


def _result(
    definition: CheckDefinition,
    passed: bool,
    *,
    attempted: bool = True,
    failure_reason: Optional[FailureReason] = None,
    message: str = "",
) -> CheckResult:
    return CheckResult(
        check_id=definition.id,
        passed=passed,
        attempted=attempted,
        mechanism=definition.mechanism,
        subject=definition.subject,
        difficulty=definition.difficulty,
        failure_reason=failure_reason,
        message=message,
        metadata={"label": definition.label, **dict(definition.metadata)},
    )
