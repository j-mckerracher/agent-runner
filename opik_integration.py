"""
Opik integration for agent-runner.

Sets up Opik from environment variables and provides helpers for:
- Loading agent system prompts from .claude/agents/*.agent.md
- Injecting referenced agent-context file contents into evaluator prompts
- Calling evaluator agents via Anthropic SDK with @opik.track

Required environment variables:
    ANTHROPIC_API_KEY           — Anthropic SDK key (required for SDK evaluator calls)
    OPIK_API_KEY                — Opik Cloud key  (omit when using a local instance)
    OPIK_WORKSPACE              — Opik workspace name (optional, Cloud only)
    OPIK_URL_OVERRIDE           — Self-hosted Opik URL, e.g. http://localhost:5173/api
    OPIK_PROJECT_NAME           — Project name shown in the dashboard (default: agent-runner)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import truststore
truststore.inject_into_ssl()

import anthropic
import httpx
import opik

RUNNER_ROOT = Path(__file__).resolve().parent
AGENTS_DIR = RUNNER_ROOT / ".claude" / "agents"

# Set project name default before any Opik decorator fires.
os.environ.setdefault("OPIK_PROJECT_NAME", "agent-runner")


# ---------------------------------------------------------------------------
# Agent system-prompt loader
# ---------------------------------------------------------------------------

def load_agent_system_prompt(agent_name: str) -> str:
    """
    Find .claude/agents/*{agent_name}*.agent.md and return its body as a
    plain-text system prompt, with frontmatter, <agent> wrapper tags, and
    HTML comments stripped.

    Raises FileNotFoundError if no matching agent file is found.
    """
    matches = sorted(AGENTS_DIR.glob(f"*{agent_name}*.agent.md"))
    if not matches:
        raise FileNotFoundError(
            f"No agent file found for '{agent_name}' in {AGENTS_DIR}"
        )
    content = matches[0].read_text(encoding="utf-8")
    content = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)   # strip YAML frontmatter
    content = re.sub(r"</?agent>", "", content)                         # strip <agent> tags
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)       # strip HTML comments
    return content.strip()


# ---------------------------------------------------------------------------
# File-content injector
# ---------------------------------------------------------------------------

def inject_file_contents(context: str) -> str:
    """
    Scan *context* for agent-context/... file paths, read each file that
    exists on disk, and return a markdown-fenced block ready to append to
    a prompt.  Returns an empty string when no files are found.
    """
    pattern = r"agent-context/[\w\-]+/[\w\-./]+"
    paths = re.findall(pattern, context)
    sections: list[str] = []
    for rel_path in paths:
        full_path = RUNNER_ROOT / rel_path
        if full_path.is_file():
            body = full_path.read_text(encoding="utf-8")
            sections.append(f"### {rel_path}\n```\n{body}\n```")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# SDK evaluator — the core Opik-traced LLM call
# ---------------------------------------------------------------------------

@opik.track(name="sdk-evaluator", type="llm")
def call_evaluator_sdk(
    context: str,
    agent_name: str,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """
    Run an evaluator agent via the Anthropic Python SDK with Opik tracing.

    1. Loads the agent's system prompt from .claude/agents/*.agent.md
    2. Reads every agent-context file referenced in *context* and appends
       their contents so the SDK call has the same information the CLI agent
       would have obtained through its file-reading tools.
    3. Calls claude via anthropic.Anthropic().messages.create() — this call
       is what Opik records as the real LLM span (tokens, latency, model).

    Args:
        context:    The evaluator prompt / instruction string.
        agent_name: Slug used to locate the .agent.md file (e.g. "task-plan-evaluator").
        model:      Anthropic model ID.

    Returns:
        The raw text response from the model.
    """
    system_prompt = load_agent_system_prompt(agent_name)
    file_block = inject_file_contents(context)
    user_message = (
        f"{context}\n\n## Referenced File Contents\n\n{file_block}"
        if file_block
        else context
    )

    _env_cert = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if os.environ.get("ANTHROPIC_VERIFY_SSL", "1") == "0":
        _verify: bool | str = False
    elif _env_cert:
        _verify = _env_cert
    else:
        _verify = True
    client = anthropic.Anthropic(http_client=httpx.Client(verify=_verify))
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
