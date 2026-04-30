from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RUNNER_ROOT = Path(__file__).resolve().parent
AGENTS_DIR = RUNNER_ROOT / ".claude" / "agents"


def strip_agent_prompt_markup(content: str) -> str:
    """Normalize a materialized agent prompt into plain instruction text."""
    original_len = len(content)
    content = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
    content = re.sub(r"</?agent>", "", content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = content.strip()
    logger.debug("strip_agent_prompt_markup: reduced %d → %d chars", original_len, len(content))
    return content


def load_agent_system_prompt(agent_name: str, prompts_dir: Path | None = None) -> str:
    """
    Find .claude/agents/*{agent_name}*.agent.md and return its body as plain text.

    Raises FileNotFoundError if no matching agent file is found.
    """
    search_dir = prompts_dir or AGENTS_DIR
    logger.debug("load_agent_system_prompt: agent_name=%s search_dir=%s", agent_name, search_dir)
    matches = sorted(search_dir.glob(f"*{agent_name}*.agent.md"))
    if not matches:
        logger.error("load_agent_system_prompt: no agent file found for %r in %s", agent_name, search_dir)
        raise FileNotFoundError(f"No agent file found for '{agent_name}' in {search_dir}")
    chosen = matches[0]
    logger.debug("load_agent_system_prompt: loading %s (of %d match(es))", chosen.name, len(matches))
    prompt = strip_agent_prompt_markup(chosen.read_text(encoding="utf-8"))
    logger.debug("load_agent_system_prompt: prompt length=%d chars for agent=%s", len(prompt), agent_name)
    return prompt
