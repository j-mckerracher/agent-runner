import os
import re
import subprocess
import time
from pathlib import Path
from prefect import task

from agent_prompts import load_agent_system_prompt
from runner_models import DEFAULT_GEMINI_MODEL

RUNNER_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = RUNNER_ROOT / ".github" / "skills"

CLAUDE_AUTH_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
})


def _without_claude_auth_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in CLAUDE_AUTH_ENV_VARS:
        env.pop(key, None)
    return env


def _load_skill_content(skill_name: str) -> str | None:
    """Load skill content from .github/skills/<skill>/SKILL.md if it exists."""
    skill_file = SKILLS_ROOT / skill_name / "SKILL.md"
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")
    return None


def _extract_required_skills(agent_spec: str) -> list[str]:
    """Extract skill names from the Required Skills table in an agent spec."""
    skills: list[str] = []
    in_skills_section = False
    for line in agent_spec.split("\n"):
        if "## Required Skills" in line:
            in_skills_section = True
            continue
        if in_skills_section:
            if line.startswith("##"):
                break
            match = re.search(r"\|\s*\*\*([a-zA-Z0-9\-]+)\*\*", line)
            if match:
                skills.append(match.group(1))
    return skills


def _build_gemini_prompt(prompt: str, agent: str, extra_skills: list[str] | None = None) -> str:
    """
    Build a combined prompt for Gemini CLI headless mode.

    Because Gemini CLI has no activate_skill mechanism, any explicitly-requested
    skill content is embedded directly. Required skills from the agent spec are NOT
    auto-embedded to avoid excessively large contexts — only pass extra_skills that
    are strictly needed for the task (e.g., tool-use skills the agent must follow).
    """
    agent_prompt = load_agent_system_prompt(agent)

    skills_section = ""
    if extra_skills:
        skill_blocks: list[str] = []
        for skill_name in extra_skills:
            content = _load_skill_content(skill_name)
            if content:
                skill_blocks.append(f"### Skill: {skill_name}\n\n{content}")
        if skill_blocks:
            skills_section = (
                "\n\n## Embedded Skill References\n\n"
                "The following skills are embedded for your use. Follow each skill's "
                "protocol as instructed by the agent specification above.\n\n"
                + "\n\n---\n\n".join(skill_blocks)
            )

    return (
        f"You are running as the '{agent}' specialist in the agent-runner workflow.\n"
        f"Treat the following agent specification as your governing instructions for this run.\n\n"
        f"## Agent specification\n{agent_prompt}"
        f"{skills_section}\n\n"
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
    extra_skills: list[str] | None = None,
) -> str:
    """
    Trigger Gemini CLI non-interactively and return stdout.

    Gemini does not expose a top-level --agent flag in the installed CLI, so
    the materialized agent prompt is injected into the headless prompt payload.
    Required skills are embedded directly since Gemini has no activate_skill mechanism.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    combined_prompt = _build_gemini_prompt(prompt=prompt, agent=agent, extra_skills=extra_skills)
    print(f"Starting Gemini CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["gemini", "-p", combined_prompt, "--model", model, "--output-format", output_format]
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    for _attempt in range(5):
        result = subprocess.run(cmd, text=True, capture_output=True, env=_without_claude_auth_env())
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        if result.returncode == 0:
            return result.stdout
        combined = (result.stdout or "") + (result.stderr or "")
        is_transient = any(
            marker in combined
            for marker in ("503", "UNAVAILABLE", "high demand", "rate limit", "429")
        )
        if is_transient and _attempt < 4:
            delay = 60 * (2 ** _attempt)
            print(f"[run_gemini_cmd] Transient error (attempt {_attempt + 1}/5). Retrying in {delay}s...")
            time.sleep(delay)
        else:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout  # unreachable

def run_agent_cmd(
    runner: str,
    prompt: str,
    agent: str,
    **kwargs,
) -> str:
    """Dispatch to the selected CLI runner based on runner."""
    runner_model = kwargs.pop("runner_model", None)
    extra_skills = kwargs.pop("extra_skills", None)
    if runner == "copilot":
        return run_copilot_cmd(prompt=prompt, agent=agent, **kwargs)
    elif runner == "claude":
        return run_claude_cmd(prompt=prompt, agent=agent, **kwargs)
    elif runner == "gemini":
        if runner_model is not None:
            kwargs.setdefault("model", runner_model)
        return run_gemini_cmd(prompt=prompt, agent=agent, extra_skills=extra_skills, **kwargs)
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
