"""
Opik integration for agent-runner.

Sets up Opik from environment variables and provides helpers for:
- Loading agent system prompts from .claude/agents/*.agent.md
- Injecting referenced agent-context file contents into evaluator prompts
- Calling evaluator agents via Anthropic SDK or Gemini SDK with @opik.track

Required environment variables:
    ANTHROPIC_API_KEY           — Anthropic SDK key (required when runner != "gemini")
    GEMINI_API_KEY              — Gemini API key (required when runner == "gemini")
    OPIK_API_KEY                — Opik Cloud key  (omit when using a local instance)
    OPIK_WORKSPACE              — Opik workspace name (optional, Cloud only)
    OPIK_URL_OVERRIDE           — Self-hosted Opik URL, e.g. http://localhost:5173/api
    OPIK_PROJECT_NAME           — Project name shown in the dashboard (default: agent-runner)
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import truststore
truststore.inject_into_ssl()

import anthropic
import httpx
import opik
from google import genai as google_genai
from agent_prompts import load_agent_system_prompt

RUNNER_ROOT = Path(__file__).resolve().parent

# Set project name default before any Opik decorator fires.
os.environ.setdefault("OPIK_PROJECT_NAME", "agent-runner")


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
    runner: str = "claude",
    runner_model: str | None = None,
) -> str:
    """
    Run an evaluator agent via the Anthropic or Gemini Python SDK with Opik tracing.

    When runner=="gemini", uses the Gemini API (GEMINI_API_KEY required).
    Otherwise, uses the Anthropic API (ANTHROPIC_API_KEY required).

    1. Loads the agent's system prompt from .claude/agents/*.agent.md
    2. Reads every agent-context file referenced in *context* and appends
       their contents so the SDK call has the same information the CLI agent
       would have obtained through its file-reading tools.
    3. Calls the LLM — this call is what Opik records as the real LLM span.

    Args:
        context:      The evaluator prompt / instruction string.
        agent_name:   Slug used to locate the .agent.md file (e.g. "task-plan-evaluator").
        model:        Model ID (Anthropic or Gemini depending on runner).
        runner:       "gemini" to use Gemini API; anything else uses Anthropic.
        runner_model: Explicit Gemini model name to use (overrides *model* when runner=="gemini").

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

    if runner == "gemini":
        if runner_model and "gemini" in runner_model:
            gemini_model = runner_model
        elif model and "gemini" in model:
            gemini_model = model
        else:
            gemini_model = "gemini-3.1-flash-lite-preview"
        client = google_genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        _transient_codes = {429, 500, 503, 529}
        for _attempt in range(5):
            try:
                response = client.models.generate_content(
                    model=gemini_model,
                    contents=f"{system_prompt}\n\n{user_message}",
                )
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                msg = str(exc)
                is_transient = status in _transient_codes or any(
                    str(c) in msg for c in _transient_codes
                )
                if is_transient and _attempt < 4:
                    delay = 30 * (2 ** _attempt)
                    print(f"[call_evaluator_sdk] Transient error (attempt {_attempt + 1}/5): {msg[:120]}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    raise
        text = response.text
        if text is None:
            # Fallback: extract text from candidates if response.text was blocked/empty
            try:
                candidates = response.candidates or []
                text = candidates[0].content.parts[0].text or ""
            except (AttributeError, IndexError, TypeError):
                text = ""
        return text

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
