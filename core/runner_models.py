import json
from pathlib import Path

GEMINI_MODEL_CHOICES = (
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
    "gemini-flash-lite-latest",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

CLAUDE_MODEL_CHOICES = (
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
)

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

COPILOT_MODEL_CHOICES = (
    "gpt-5-mini",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-4.1",
    "claude-opus-4.7",
    "claude-sonnet-4.6",
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
)

COPILOT_EFFORT_CHOICES = ("low", "medium", "high", "xhigh")
DEFAULT_COPILOT_EFFORT: str | None = None  # omit flag unless explicitly set

DEFAULT_COPILOT_MODEL = "gpt-5-mini"

RUNNER_MODEL_CHOICES: dict[str, tuple[str, ...]] = {
    "claude":  CLAUDE_MODEL_CHOICES,
    "copilot": COPILOT_MODEL_CHOICES,
    "gemini":  GEMINI_MODEL_CHOICES,
}

RUNNER_DEFAULT_MODELS: dict[str, str] = {
    "claude":  DEFAULT_CLAUDE_MODEL,
    "copilot": DEFAULT_COPILOT_MODEL,
    "gemini":  DEFAULT_GEMINI_MODEL,
}


# ─── Copilot alias helpers ────────────────────────────────────────────────────

def is_copilot_runner(runner: str) -> bool:
    """Return True for 'copilot' or any 'copilot-<alias>' runner string."""
    return runner == "copilot" or runner.startswith("copilot-")


def canonical_runner(runner: str) -> str:
    """Return the base runner name; maps any 'copilot-<alias>' to 'copilot'."""
    if runner.startswith("copilot-"):
        return "copilot"
    return runner


def copilot_alias_model(runner: str) -> str:
    """Return the model name component of a copilot alias runner string.

    This is used for display/reporting purposes only. Alias runners (e.g.
    'copilot-gemma4') are invoked as their own CLI binary and do NOT receive
    a --model flag — the binary itself defines the model.

    'copilot-gemma4'  → 'gemma4'
    'copilot'         → DEFAULT_COPILOT_MODEL
    """
    if runner.startswith("copilot-"):
        return runner[len("copilot-"):]
    return DEFAULT_COPILOT_MODEL


def discover_copilot_aliases() -> list[str]:
    """Discover locally configured copilot alias runners from ~/.copilot/aliases.json.

    Each alias names a standalone CLI binary available on PATH (e.g. 'copilot-gemma4'
    is a binary that wraps the Copilot harness with gemma4 running in Ollama cloud).
    The binary is invoked directly — no ``--model`` flag is added.

    The file should be a JSON array of alias name strings, e.g.::

        ["gemma4", "gpt5", "claude-sonnet"]

    Returns a list of full runner strings like ``["copilot-gemma4", ...]``.
    Returns ``[]`` when the file is absent or malformed.
    """
    aliases_path = Path.home() / ".copilot" / "aliases.json"
    if not aliases_path.exists():
        return []
    try:
        data = json.loads(aliases_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                f"copilot-{alias}"
                for alias in data
                if isinstance(alias, str) and alias.strip()
            ]
    except Exception:  # noqa: BLE001
        pass
    return []


def resolve_agent_model(
    agent_name: str,
    runner: str,
    explicit_model: str | None = None,
    config: dict | None = None,
) -> str:
    """
    Resolve the model for a given agent and runner.

    Precedence:
    1. Explicit model provided by caller
    2. Per-agent + per-runner configured default (from config.agent_model_defaults)
    3. For copilot aliases: the alias suffix is the implicit model
    4. Built-in runner default from RUNNER_DEFAULT_MODELS

    Args:
        agent_name:     Name of the agent (e.g., "task-plan-evaluator", "intake").
        runner:         Runner to use ("claude", "copilot", "gemini", or "copilot-<alias>").
        explicit_model: If provided, this takes highest precedence.
        config:         Optional config dict with agent_model_defaults structure.
                        Expected format: {"agent_model_defaults": {agent_name: {runner: model}}}

    Returns:
        The resolved model string to use.

    Raises:
        ValueError if runner is unknown.
    """
    if explicit_model is not None:
        return explicit_model

    base_runner = canonical_runner(runner)

    if config is not None:
        agent_defaults = config.get("agent_model_defaults", {})
        if agent_name in agent_defaults:
            runner_defaults = agent_defaults[agent_name]
            # Prefer exact runner key (e.g. "copilot-gemma4"), fall back to base
            if runner in runner_defaults:
                return runner_defaults[runner]
            if base_runner in runner_defaults:
                return runner_defaults[base_runner]

    if base_runner not in RUNNER_DEFAULT_MODELS:
        raise ValueError(f"Unknown runner: {runner!r}. Must be one of {list(RUNNER_DEFAULT_MODELS.keys())}")

    # For copilot aliases the alias suffix encodes the model
    if is_copilot_runner(runner) and runner != "copilot":
        return copilot_alias_model(runner)

    return RUNNER_DEFAULT_MODELS[base_runner]
