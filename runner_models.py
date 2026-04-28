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
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

COPILOT_MODEL_CHOICES = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5-mini",
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
