import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from agent_prompts import load_agent_system_prompt
from runner_models import DEFAULT_GEMINI_MODEL

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Optional event emission + cassette recording.
# Both are activated only when the corresponding env var is set, so the CLI
# remains side-effect-free for direct users.
# ─────────────────────────────────────────────────────────────────────────────


def _emit_event(type: str, **fields) -> None:
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


def _record_cassette(**fields) -> None:
    if not os.environ.get("AGENT_RUNNER_CASSETTE"):
        return
    try:
        from server.cassette import record
        record(**fields)
    except Exception:
        pass


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _summarize_cli_failure(result: subprocess.CompletedProcess) -> str:
    detail = _last_nonempty_line(result.stderr or "") or _last_nonempty_line(result.stdout or "")
    if detail:
        return detail[:500]
    return f"Process exited with code {result.returncode}"


def _run_cli(
    cmd: list[str],
    *,
    runner: str,
    agent: str,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run that emits structured events / cassette records.

    Behavior is identical to subprocess.run when AGENT_RUNNER_EVENT_LOG and
    AGENT_RUNNER_CASSETTE are unset.
    """
    logger.info("_run_cli: runner=%s agent=%s cmd=%s", runner, agent, cmd[0])
    logger.debug("_run_cli: full cmd=%s", cmd)
    _emit_event(
        "cli.invoke",
        runner=runner,
        agent=agent,
        cmd=list(cmd[:1]),
        argc=len(cmd) - 1,
    )
    start = time.monotonic()
    if env is None:
        result = subprocess.run(cmd, text=True, capture_output=True)
    else:
        result = subprocess.run(cmd, text=True, capture_output=True, env=env)
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "_run_cli: runner=%s agent=%s exit_code=%d duration_ms=%d",
        runner, agent, result.returncode, duration_ms,
    )
    _emit_event(
        "cli.exit",
        runner=runner,
        agent=agent,
        exit_code=result.returncode,
        duration_ms=duration_ms,
    )
    if result.returncode != 0:
        summary = _summarize_cli_failure(result)
        logger.error(
            "_run_cli: FAILED runner=%s agent=%s exit_code=%d: %s",
            runner, agent, result.returncode, summary,
        )
        _emit_event(
            "log",
            level="error",
            kind="command_failed",
            runner=runner,
            agent=agent,
            msg=f"{agent} command failed (exit {result.returncode}): {summary}",
        )
    else:
        logger.debug("_run_cli: SUCCESS runner=%s agent=%s", runner, agent)
    _record_cassette(
        cmd=cmd,
        stdin=None,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        duration_ms=duration_ms,
        stage=agent,
        extra={"runner": runner},
    )
    return result

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
    logger.debug("_without_claude_auth_env: stripped %s from env", CLAUDE_AUTH_ENV_VARS)
    return env


def _load_skill_content(skill_name: str) -> str | None:
    """Load skill content from .github/skills/<skill>/SKILL.md if it exists."""
    skill_file = SKILLS_ROOT / skill_name / "SKILL.md"
    if skill_file.exists():
        content = skill_file.read_text(encoding="utf-8")
        logger.debug("_load_skill_content: loaded skill=%s (%d chars)", skill_name, len(content))
        return content
    logger.debug("_load_skill_content: skill file not found for %s at %s", skill_name, skill_file)
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
    logger.debug("_extract_required_skills: found skills=%s", skills)
    return skills


def _build_gemini_prompt(prompt: str, agent: str, extra_skills: list[str] | None = None) -> str:
    """
    Build a combined prompt for Gemini CLI headless mode.

    Because Gemini CLI has no activate_skill mechanism, any explicitly-requested
    skill content is embedded directly. Required skills from the agent spec are NOT
    auto-embedded to avoid excessively large contexts — only pass extra_skills that
    are strictly needed for the task (e.g., tool-use skills the agent must follow).
    """
    logger.debug("_build_gemini_prompt: agent=%s extra_skills=%s", agent, extra_skills)
    agent_prompt = load_agent_system_prompt(agent)

    skills_section = ""
    if extra_skills:
        skill_blocks: list[str] = []
        for skill_name in extra_skills:
            content = _load_skill_content(skill_name)
            if content:
                skill_blocks.append(f"### Skill: {skill_name}\n\n{content}")
            else:
                logger.warning("_build_gemini_prompt: skill=%s not found; skipping embed", skill_name)
        if skill_blocks:
            logger.debug("_build_gemini_prompt: embedding %d skill(s) for agent=%s", len(skill_blocks), agent)
            skills_section = (
                "\n\n## Embedded Skill References\n\n"
                "The following skills are embedded for your use. Follow each skill's "
                "protocol as instructed by the agent specification above.\n\n"
                + "\n\n---\n\n".join(skill_blocks)
            )

    combined = (
        f"You are running as the '{agent}' specialist in the agent-runner workflow.\n"
        f"Treat the following agent specification as your governing instructions for this run.\n\n"
        f"## Agent specification\n{agent_prompt}"
        f"{skills_section}\n\n"
        f"## Task to execute\n{prompt}"
    )
    logger.debug("_build_gemini_prompt: combined prompt length=%d chars for agent=%s", len(combined), agent)
    return combined

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
    logger.info("run_claude_cmd: agent=%s model=%s prompt_len=%d", agent, model, len(prompt))
    print(f"Starting Claude Code via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["claude", "-p", prompt, "--agent", agent, "--model", model, "--output-format", "json"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_flags:
        cmd.extend(extra_flags)
    result = _run_cli(cmd, runner="claude", agent=agent)
    stdout_raw = result.stdout or ""
    text_out = stdout_raw
    try:
        parsed = json.loads(stdout_raw)
        ti = int(parsed.get("total_input_tokens") or 0)
        to = int(parsed.get("total_output_tokens") or 0)
        cu = float(parsed.get("cost_usd") or 0.0)
        if cu == 0.0 and (ti > 0 or to > 0):
            cu = round((ti / 1_000_000 * 3.0) + (to / 1_000_000 * 15.0), 6)
        if ti > 0 or to > 0:
            _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=cu)
        text_out = str(parsed.get("result") or stdout_raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("run_claude_cmd: could not parse JSON output for agent=%s", agent)
    if text_out:
        logger.debug("run_claude_cmd: stdout length=%d for agent=%s", len(text_out), agent)
        print(text_out)
    if result.stderr:
        logger.debug("run_claude_cmd: stderr length=%d for agent=%s", len(result.stderr), agent)
        print(result.stderr)
    if result.returncode != 0:
        logger.error("run_claude_cmd: agent=%s exited %d", agent, result.returncode)
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    logger.info("run_claude_cmd: agent=%s completed OK", agent)
    return text_out


def run_claude(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_claude_cmd."""
    return run_claude_cmd(prompt=prompt, agent=agent, model=model,
                          skip_permissions=skip_permissions, extra_flags=extra_flags)


def run_copilot_cmd(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    effort: str | None = None,
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
    logger.info("run_copilot_cmd: agent=%s model=%s effort=%s prompt_len=%d", agent, model, effort, len(prompt))
    print(f"Starting Copilot CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    if effort:
        print(f"Effort: {effort}")
    cmd = ["copilot", "-p", prompt, f"--agent={agent}", "--model", model]
    if effort:
        cmd.extend(["--effort", effort])
    if silent:
        cmd.append("-s")
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    result = _run_cli(cmd, runner="copilot", agent=agent, env=_without_claude_auth_env())
    if result.stdout:
        logger.debug("run_copilot_cmd: stdout length=%d for agent=%s", len(result.stdout), agent)
        print(result.stdout)
    if result.stderr:
        logger.debug("run_copilot_cmd: stderr length=%d for agent=%s", len(result.stderr), agent)
        print(result.stderr)
    if result.returncode != 0:
        logger.error("run_copilot_cmd: agent=%s exited %d", agent, result.returncode)
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    logger.info("run_copilot_cmd: agent=%s completed OK", agent)
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
    logger.info("run_gemini_cmd: agent=%s model=%s prompt_len=%d", agent, model, len(prompt))
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
        logger.debug("run_gemini_cmd: attempt %d/5 agent=%s", _attempt + 1, agent)
        result = _run_cli(cmd, runner="gemini", agent=agent, env=_without_claude_auth_env())
        if result.stdout:
            logger.debug("run_gemini_cmd: stdout length=%d for agent=%s", len(result.stdout), agent)
            print(result.stdout)
        if result.stderr:
            logger.debug("run_gemini_cmd: stderr length=%d for agent=%s", len(result.stderr), agent)
            print(result.stderr)
        if result.returncode == 0:
            logger.info("run_gemini_cmd: agent=%s completed OK on attempt %d", agent, _attempt + 1)
            return result.stdout
        combined = (result.stdout or "") + (result.stderr or "")
        is_transient = any(
            marker in combined
            for marker in ("503", "UNAVAILABLE", "high demand", "rate limit", "429")
        )
        if is_transient and _attempt < 4:
            delay = 60 * (2 ** _attempt)
            logger.warning(
                "run_gemini_cmd: transient error on attempt %d/5 for agent=%s; retrying in %ds",
                _attempt + 1, agent, delay,
            )
            print(f"[run_gemini_cmd] Transient error (attempt {_attempt + 1}/5). Retrying in {delay}s...")
            time.sleep(delay)
        else:
            logger.error(
                "run_gemini_cmd: agent=%s failed after %d attempt(s) exit_code=%d",
                agent, _attempt + 1, result.returncode,
            )
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result.stdout  # unreachable


def run_agent_cmd(
    runner: str,
    prompt: str,
    agent: str,
    **kwargs,
) -> str:
    """Dispatch to the selected CLI runner based on runner."""
    logger.debug("run_agent_cmd: runner=%s agent=%s", runner, agent)
    runner_model = kwargs.pop("runner_model", None)
    extra_skills = kwargs.pop("extra_skills", None)
    copilot_effort = kwargs.pop("copilot_effort", None)
    model_kwarg = {"model": runner_model} if runner_model is not None else {}
    if runner == "copilot":
        effort_kwarg = {"effort": copilot_effort} if copilot_effort is not None else {}
        return run_copilot_cmd(prompt=prompt, agent=agent, **model_kwarg, **effort_kwarg, **kwargs)
    elif runner == "claude":
        return run_claude_cmd(prompt=prompt, agent=agent, **model_kwarg, **kwargs)
    elif runner == "gemini":
        return run_gemini_cmd(prompt=prompt, agent=agent, extra_skills=extra_skills, **model_kwarg, **kwargs)
    else:
        logger.error("run_agent_cmd: unknown runner=%r", runner)
        raise ValueError(f"Unknown runner: {runner!r}. Must be 'claude', 'copilot', or 'gemini'.")


def run_copilot(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_copilot_cmd."""
    return run_copilot_cmd(prompt=prompt, agent=agent, model=model,
                           silent=silent, extra_flags=extra_flags)


def run_gemini(
    prompt: str,
    agent: str,
    model: str = DEFAULT_GEMINI_MODEL,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_gemini_cmd."""
    return run_gemini_cmd(prompt=prompt, agent=agent, model=model, extra_flags=extra_flags)
