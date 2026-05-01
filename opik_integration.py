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

import logging
import os
import re
import time
from pathlib import Path

import truststore
truststore.inject_into_ssl()

import anthropic
import httpx
from opik import opik_context
from google import genai as google_genai
from agent_prompts import load_agent_system_prompt
from ui_trace_bridge import track_with_ui
from run_cmds import run_copilot_cmd

logger = logging.getLogger(__name__)

RUNNER_ROOT = Path(__file__).resolve().parent

_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}
_DEFAULT_PRICING = (3.0, 15.0)  # Sonnet 4.6 long-session rate


def _emit_event(type: str, **fields) -> None:
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass

# Set project name default before any Opik decorator fires.
os.environ.setdefault("OPIK_PROJECT_NAME", "agent-runner")
logger.debug("opik_integration: OPIK_PROJECT_NAME=%s", os.environ.get("OPIK_PROJECT_NAME"))


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
    logger.debug("inject_file_contents: found %d path reference(s) in context", len(paths))
    sections: list[str] = []
    for rel_path in paths:
        full_path = RUNNER_ROOT / rel_path
        if full_path.is_file():
            body = full_path.read_text(encoding="utf-8")
            sections.append(f"### {rel_path}\n```\n{body}\n```")
            logger.debug("inject_file_contents: injected %s (%d chars)", rel_path, len(body))
        else:
            logger.debug("inject_file_contents: path not found on disk: %s", full_path)
    result = "\n\n".join(sections)
    logger.debug("inject_file_contents: total injected content=%d chars from %d file(s)", len(result), len(sections))
    return result


# ---------------------------------------------------------------------------
# SDK evaluator — the core Opik-traced LLM call
# ---------------------------------------------------------------------------

def _extract_change_id(context: str) -> str:
    match = re.search(r"agent-context/([\w\-]+)/", context)
    return match.group(1) if match else ""


def _sdk_trace_metadata(
    context: str,
    agent_name: str,
    model: str = "claude-haiku-4-5-20251001",
    runner: str = "claude",
    runner_model: str | None = None,
) -> dict[str, str]:
    resolved_model = runner_model if runner == "gemini" and runner_model else model
    return {
        "agent": agent_name,
        "change_id": _extract_change_id(context),
        "runner": runner,
        "model": resolved_model,
    }


@track_with_ui(name="sdk-evaluator", type="llm", metadata_getter=_sdk_trace_metadata)
def call_evaluator_sdk(
    context: str,
    agent_name: str,
    model: str = "claude-haiku-4-5-20251001",
    runner: str = "claude",
    runner_model: str | None = None,
    copilot_effort: str | None = None,
) -> str:
    """
    Run an evaluator agent via Copilot CLI, Anthropic SDK, or Gemini SDK with Opik tracing.

    When runner=="copilot", uses Copilot CLI with the selected model.
    When runner=="gemini", uses the Gemini API (GEMINI_API_KEY required).
    When runner=="claude", uses the Anthropic API (ANTHROPIC_API_KEY required).
    Unknown runners raise ValueError.

    1. Loads the agent's system prompt from .claude/agents/*.agent.md
    2. Reads every agent-context file referenced in *context* and appends
       their contents so the CLI agent has the same information it would
       have obtained through its file-reading tools.
    3. Calls the LLM — this call is what Opik records as the real LLM span.

    Args:
        context:       The evaluator prompt / instruction string.
        agent_name:    Slug used to locate the .agent.md file (e.g. "task-plan-evaluator").
        model:         Model ID (Anthropic, Copilot, or Gemini depending on runner).
        runner:        One of "copilot", "claude", or "gemini".
        runner_model:  Explicit model name to use (overrides *model* when runner=="gemini").
        copilot_effort: Optional effort level for Copilot ("low", "medium", "high").

    Returns:
        The raw text response from the model.
    """
    logger.info("call_evaluator_sdk: agent_name=%s runner=%s model=%s runner_model=%s", agent_name, runner, model, runner_model)
    system_prompt = load_agent_system_prompt(agent_name)
    file_block = inject_file_contents(context)
    user_message = (
        f"{context}\n\n## Referenced File Contents\n\n{file_block}"
        if file_block
        else context
    )
    logger.debug("call_evaluator_sdk: user_message length=%d agent=%s", len(user_message), agent_name)

    if runner == "copilot":
        logger.info("call_evaluator_sdk: using Copilot CLI model=%s effort=%s agent=%s", model, copilot_effort, agent_name)
        text = run_copilot_cmd(
            prompt=user_message,
            agent=agent_name,
            model=model,
            effort=copilot_effort,
        )
        logger.info("call_evaluator_sdk: Copilot CLI call succeeded agent=%s response_len=%d", agent_name, len(text or ""))
        _attach_usage_metadata(None, provider="copilot", model=model)
        return text

    elif runner == "claude":
        logger.info("call_evaluator_sdk: using Anthropic API model=%s agent=%s", model, agent_name)
        _env_cert = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if os.environ.get("ANTHROPIC_VERIFY_SSL", "1") == "0":
            _verify: bool | str = False
            logger.debug("call_evaluator_sdk: SSL verification disabled (ANTHROPIC_VERIFY_SSL=0)")
        elif _env_cert:
            _verify = _env_cert
            logger.debug("call_evaluator_sdk: using custom SSL cert %s", _env_cert)
        else:
            _verify = True
        client = anthropic.Anthropic(http_client=httpx.Client(verify=_verify))
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text
        logger.info("call_evaluator_sdk: Anthropic call succeeded agent=%s response_len=%d", agent_name, len(text or ""))
        _attach_usage_metadata(response, provider="anthropic", model=model)
        return text

    elif runner == "gemini":
        if runner_model and "gemini" in runner_model:
            gemini_model = runner_model
        elif model and "gemini" in model:
            gemini_model = model
        else:
            gemini_model = "gemini-3.1-flash-lite-preview"
        logger.info("call_evaluator_sdk: using Gemini API model=%s agent=%s", gemini_model, agent_name)
        client = google_genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        _transient_codes = {429, 500, 503, 529}
        for _attempt in range(5):
            logger.debug("call_evaluator_sdk: Gemini attempt %d/5 agent=%s", _attempt + 1, agent_name)
            try:
                response = client.models.generate_content(
                    model=gemini_model,
                    contents=f"{system_prompt}\n\n{user_message}",
                )
                logger.info("call_evaluator_sdk: Gemini call succeeded on attempt %d for agent=%s", _attempt + 1, agent_name)
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                msg = str(exc)
                is_transient = status in _transient_codes or any(
                    str(c) in msg for c in _transient_codes
                )
                if is_transient and _attempt < 4:
                    delay = 30 * (2 ** _attempt)
                    logger.warning(
                        "call_evaluator_sdk: Gemini transient error attempt %d/5 agent=%s: %s. Retrying in %ds",
                        _attempt + 1, agent_name, msg[:120], delay,
                    )
                    print(f"[call_evaluator_sdk] Transient error (attempt {_attempt + 1}/5): {msg[:120]}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(
                        "call_evaluator_sdk: Gemini non-transient failure after %d attempt(s) agent=%s: %s",
                        _attempt + 1, agent_name, msg[:200],
                    )
                    raise
        text = response.text
        if text is None:
            logger.warning("call_evaluator_sdk: Gemini response.text was None for agent=%s; extracting from candidates", agent_name)
            # Fallback: extract text from candidates if response.text was blocked/empty
            try:
                candidates = response.candidates or []
                text = candidates[0].content.parts[0].text or ""
            except (AttributeError, IndexError, TypeError):
                logger.warning("call_evaluator_sdk: candidate extraction failed for agent=%s; returning empty", agent_name)
                text = ""
        logger.debug("call_evaluator_sdk: Gemini response length=%d for agent=%s", len(text or ""), agent_name)
        _attach_usage_metadata(response, provider="gemini", model=gemini_model)
        return text

    else:
        raise ValueError(f"Unknown runner={runner} for call_evaluator_sdk; must be one of 'copilot', 'claude', 'gemini'")


def _attach_usage_metadata(response, *, provider: str, model: str = "") -> None:
    """
    Attach token usage to the current Opik span when the response carries it.

    Anthropic responses expose `response.usage` (input_tokens / output_tokens).
    Gemini responses expose `response.usage_metadata`
    (prompt_token_count / candidates_token_count / total_token_count).

    Failures are swallowed so observability never breaks the LLM call.
    """
    try:
        usage_payload: dict = {}
        if provider == "anthropic":
            usage = getattr(response, "usage", None)
            if usage is None:
                return
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            if input_tokens is not None:
                usage_payload["prompt_tokens"] = int(input_tokens)
            if output_tokens is not None:
                usage_payload["completion_tokens"] = int(output_tokens)
            if input_tokens is not None and output_tokens is not None:
                usage_payload["total_tokens"] = int(input_tokens) + int(output_tokens)
        elif provider == "gemini":
            meta = getattr(response, "usage_metadata", None)
            if meta is None:
                return
            prompt = getattr(meta, "prompt_token_count", None)
            completion = getattr(meta, "candidates_token_count", None)
            total = getattr(meta, "total_token_count", None)
            if prompt is not None:
                usage_payload["prompt_tokens"] = int(prompt)
            if completion is not None:
                usage_payload["completion_tokens"] = int(completion)
            if total is not None:
                usage_payload["total_tokens"] = int(total)

        if usage_payload:
            logger.debug("_attach_usage_metadata: provider=%s %s", provider, usage_payload)
            opik_context.update_current_span(usage=usage_payload)
            ti = usage_payload.get("prompt_tokens", 0)
            to = usage_payload.get("completion_tokens", 0)
            in_rate, out_rate = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
            cost = round((ti / 1_000_000 * in_rate) + (to / 1_000_000 * out_rate), 6)
            _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=cost)
    except Exception as exc:
        # Never let observability errors propagate.
        logger.debug("_attach_usage_metadata: swallowed error: %s", exc)
        return
