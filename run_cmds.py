import os
import subprocess
from prefect import task

from agent_prompts import load_agent_system_prompt
from runner_models import DEFAULT_GEMINI_MODEL

CLAUDE_AUTH_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
})


def _without_claude_auth_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in CLAUDE_AUTH_ENV_VARS:
        env.pop(key, None)
    return env


def _build_gemini_prompt(prompt: str, agent: str) -> str:
    agent_prompt = load_agent_system_prompt(agent)
    return (
        f"You are running as the '{agent}' specialist in the agent-runner workflow.\n"
        f"Treat the following agent specification as your governing instructions for this run.\n\n"
        f"## Agent specification\n{agent_prompt}\n\n"
        f"## Task to execute\n{prompt}"
    )

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
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
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
    result = subprocess.run(cmd, text=True, capture_output=True, env=_without_claude_auth_env())
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout


def run_gemini_cmd(
    prompt: str,
    agent: str,
    model: str = DEFAULT_GEMINI_MODEL,
    skip_permissions: bool = True,
    output_format: str = "text",
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger Gemini CLI non-interactively and return stdout.

    Gemini does not expose a top-level --agent flag in the installed CLI, so
    the materialized agent prompt is injected into the headless prompt payload.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    combined_prompt = _build_gemini_prompt(prompt=prompt, agent=agent)
    print(f"Starting Gemini CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["gemini", "-p", combined_prompt, "--model", model, "--output-format", output_format]
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, text=True, capture_output=True, env=_without_claude_auth_env())
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout

def run_agent_cmd(
    runner: str,
    prompt: str,
    agent: str,
    **kwargs,
) -> str:
    """Dispatch to the selected CLI runner based on runner."""
    runner_model = kwargs.pop("runner_model", None)
    if runner == "copilot":
        return run_copilot_cmd(prompt=prompt, agent=agent, **kwargs)
    elif runner == "claude":
        return run_claude_cmd(prompt=prompt, agent=agent, **kwargs)
    elif runner == "gemini":
        if runner_model is not None:
            kwargs.setdefault("model", runner_model)
        return run_gemini_cmd(prompt=prompt, agent=agent, **kwargs)
    else:
        raise ValueError(f"Unknown runner: {runner!r}. Must be 'claude', 'copilot', or 'gemini'.")


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


@task(log_prints=True, name="run-gemini")
def run_gemini(
    prompt: str,
    agent: str,
    model: str = DEFAULT_GEMINI_MODEL,
    extra_flags: list[str] | None = None,
) -> str:
    """Prefect task wrapper around run_gemini_cmd."""
    return run_gemini_cmd(prompt=prompt, agent=agent, model=model, extra_flags=extra_flags)
