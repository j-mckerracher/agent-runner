from prefect import task
import subprocess

def run_claude_cmd(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger Claude Code via the CLI and return stdout.

    Args:
        prompt: The prompt or ADO URL to pass with -p.
        agent: The --agent value.
        model: The --model value.
        skip_permissions: Whether to include --dangerously-skip-permissions.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    print(f"Starting Claude Code via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["claude", "-p", prompt, "--agent", agent, "--model", model]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.stdout


@task(log_prints=True, name="run-claude")
def run_claude(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Prefect task wrapper around run_claude_cmd."""
    return run_claude_cmd(prompt=prompt, agent=agent, model=model,
                          skip_permissions=skip_permissions, extra_flags=extra_flags)


def run_copilot_cmd(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    skip_permissions: bool = True,
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger GitHub Copilot CLI non-interactively and return stdout.

    Args:
        prompt: The prompt to pass with -p.
        agent: The --agent=<name> value.
        model: The --model value.
        silent: When True, passes -s to suppress usage info from stdout.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
        :param skip_permissions:
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    print(f"Starting Copilot CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["copilot", "-p", prompt, f"--agent={agent}", "--model", model]
    if silent:
        cmd.append("-s")
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.stdout

def run_agent_cmd(
    runner: str,
    prompt: str,
    agent: str,
    **kwargs,
) -> str:
    """Dispatch to run_claude_cmd or run_copilot_cmd based on runner."""
    if runner == "copilot":
        return run_copilot_cmd(prompt=prompt, agent=agent, **kwargs)
    elif runner == "claude":
        return run_claude_cmd(prompt=prompt, agent=agent, **kwargs)
    else:
        raise ValueError(f"Unknown runner: {runner!r}. Must be 'claude' or 'copilot'.")


@task(log_prints=True, name="run-copilot")
def run_copilot(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Prefect task wrapper around run_copilot_cmd."""
    return run_copilot_cmd(prompt=prompt, agent=agent, model=model,
                           silent=silent, extra_flags=extra_flags)