from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .agent_catalog import is_disabled_agent
from .materialized_paths import runner_agent_dir

logger = logging.getLogger(__name__)

PROMPT_OVERRIDES_ENV = "AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDES"
LEGACY_PROMPT_OVERRIDES_ENV = "AGENT_RUNNER_PROMPT_OVERRIDES"


def _front_matter_agent_name(content: str) -> str | None:
    match = re.match(r"^---\s*\n(.*?)\n---\s*", content, flags=re.DOTALL)
    if not match:
        return None
    front_matter = match.group(1)
    name_match = re.search(r"^name:\s*(.+?)\s*$", front_matter, flags=re.MULTILINE)
    if not name_match:
        return None
    return name_match.group(1).strip().strip("'\"")


def _prompt_override_suffix(agent_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", agent_name).strip("_").upper()


def prompt_override_env_name(agent_name: str) -> str:
    """Environment variable name for a one-agent prompt override file."""
    return f"AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDE_{_prompt_override_suffix(agent_name)}"


def _load_json_prompt_overrides(env_name: str) -> dict[str, str]:
    value = os.environ.get(env_name)
    if not value:
        return {}
    try:
        payload: Any = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must be a JSON object mapping agent names to prompt paths") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{env_name} must be a JSON object mapping agent names to prompt paths")
    result: dict[str, str] = {}
    for agent, path in payload.items():
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError(f"{env_name} keys must be non-empty strings")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"{env_name}[{agent!r}] must be a non-empty file path")
        result[agent.strip()] = path.strip()
    return result


def agent_prompt_override_path(agent_name: str) -> Path | None:
    """Return a runtime prompt override path for an agent, if configured.

    Supported keys, in precedence order:
      - AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDES JSON mapping
      - AGENT_RUNNER_PROMPT_OVERRIDES JSON mapping (legacy)
      - AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDE_<AGENT>
      - AGENT_RUNNER_PROMPT_OVERRIDE_<AGENT> (legacy)
      - AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDE
      - AGENT_RUNNER_PROMPT_OVERRIDE (legacy)
    """
    for env_name in (PROMPT_OVERRIDES_ENV, LEGACY_PROMPT_OVERRIDES_ENV):
        mapping = _load_json_prompt_overrides(env_name)
        if agent_name in mapping:
            return Path(mapping[agent_name]).expanduser().resolve()

    suffix = _prompt_override_suffix(agent_name)
    keys = (
        prompt_override_env_name(agent_name),
        f"AGENT_RUNNER_PROMPT_OVERRIDE_{suffix}",
        "AGENT_WORKBENCH_AGENT_PROMPT_OVERRIDE",
        "AGENT_RUNNER_PROMPT_OVERRIDE",
    )
    for key in keys:
        raw = os.environ.get(key, "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return None


def has_agent_prompt_override(agent_name: str) -> bool:
    path = agent_prompt_override_path(agent_name)
    return bool(path and path.is_file())


def strip_agent_prompt_markup(content: str) -> str:
    """Normalize a materialized agent prompt into plain instruction text."""
    original_len = len(content)
    content = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
    content = re.sub(r"</?agent>", "", content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = content.strip()
    logger.debug("strip_agent_prompt_markup: reduced %d -> %d chars", original_len, len(content))
    return content


def load_agent_system_prompt(
    agent_name: str,
    prompts_dir: Path | None = None,
    *,
    runner: str = "claude",
) -> str:
    """Load a materialized agent prompt or a runtime eval prompt override."""
    if is_disabled_agent(agent_name):
        logger.error("load_agent_system_prompt: disabled agent requested: %s", agent_name)
        raise RuntimeError(f"Agent {agent_name!r} is disabled and may not be invoked")

    override_path = agent_prompt_override_path(agent_name)
    if override_path is not None:
        if not override_path.is_file():
            raise FileNotFoundError(f"Prompt override for {agent_name!r} does not exist: {override_path}")
        logger.info("load_agent_system_prompt: using prompt override for %s from %s", agent_name, override_path)
        return strip_agent_prompt_markup(override_path.read_text(encoding="utf-8"))

    search_dir = prompts_dir or runner_agent_dir(runner)
    logger.debug(
        "load_agent_system_prompt: agent_name=%s runner=%s search_dir=%s",
        agent_name,
        runner,
        search_dir,
    )
    matches = sorted(search_dir.glob(f"*{agent_name}*.agent.md"))
    if not matches:
        for candidate in sorted(search_dir.glob("*.agent.md")):
            candidate_content = candidate.read_text(encoding="utf-8")
            if _front_matter_agent_name(candidate_content) == agent_name:
                matches.append(candidate)
        if not matches:
            logger.error(
                "load_agent_system_prompt: no agent file found for %r in %s (runner=%s)",
                agent_name,
                search_dir,
                runner,
            )
            raise FileNotFoundError(f"No agent file found for '{agent_name}' in {search_dir}")
    chosen = matches[0]
    logger.debug("load_agent_system_prompt: loading %s (of %d match(es))", chosen.name, len(matches))
    prompt = strip_agent_prompt_markup(chosen.read_text(encoding="utf-8"))
    logger.debug("load_agent_system_prompt: prompt length=%d chars for agent=%s", len(prompt), agent_name)
    return prompt
