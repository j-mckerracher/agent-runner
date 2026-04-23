from __future__ import annotations

import re
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parent
AGENTS_DIR = RUNNER_ROOT / ".claude" / "agents"


def strip_agent_prompt_markup(content: str) -> str:
    """Normalize a materialized agent prompt into plain instruction text."""
    content = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
    content = re.sub(r"</?agent>", "", content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    return content.strip()


def load_agent_system_prompt(agent_name: str, prompts_dir: Path | None = None) -> str:
    """
    Find .claude/agents/*{agent_name}*.agent.md and return its body as plain text.

    Raises FileNotFoundError if no matching agent file is found.
    """
    search_dir = prompts_dir or AGENTS_DIR
    matches = sorted(search_dir.glob(f"*{agent_name}*.agent.md"))
    if not matches:
        raise FileNotFoundError(f"No agent file found for '{agent_name}' in {search_dir}")
    return strip_agent_prompt_markup(matches[0].read_text(encoding="utf-8"))
