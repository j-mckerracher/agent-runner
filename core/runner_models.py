from __future__ import annotations

import os
from typing import Any

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

DEFAULT_COPILOT_MODEL = "gpt-5-mini"

RUNNER_MODEL_CHOICES: dict[str, tuple[str, ...]] = {
    "claude": CLAUDE_MODEL_CHOICES,
    "copilot": COPILOT_MODEL_CHOICES,
    "gemini": GEMINI_MODEL_CHOICES,
}

RUNNER_DEFAULT_MODELS: dict[str, str] = {
    "claude": DEFAULT_CLAUDE_MODEL,
    "copilot": DEFAULT_COPILOT_MODEL,
    "gemini": DEFAULT_GEMINI_MODEL,
}

RUNNER_MODEL_PROVIDERS: dict[str, str] = {}

RUNNER_ALIAS_LLM_OPTION_KEYS = (
    "base_url",
    "api_version",
    "extra_headers",
    "litellm_extra_body",
    "num_retries",
    "retry_multiplier",
    "retry_min_wait",
    "retry_max_wait",
    "timeout",
)

# Known provider keys (used by server validation, GUI dropdowns, etc.)
KNOWN_RUNNERS = frozenset(RUNNER_DEFAULT_MODELS.keys())

OPENAI_COMPAT_RETRY_DEFAULTS: dict[str, Any] = {
    "num_retries": 8,
    "retry_multiplier": 2.0,
    "retry_min_wait": 8,
    "retry_max_wait": 120,
    "timeout": 420,
}


def _runner_aliases(config: dict | None) -> dict[str, dict[str, Any]]:
    aliases = (config or {}).get("runner_aliases", {})
    return aliases if isinstance(aliases, dict) else {}


def _resolve_alias(runner: str, config: dict | None) -> dict[str, Any] | None:
    alias = _runner_aliases(config).get(runner)
    return alias if isinstance(alias, dict) else None


def _default_transport_for_alias(alias: dict[str, Any]) -> dict[str, Any]:
    provider = alias.get("provider")
    if provider == "openai-compat":
        return dict(OPENAI_COMPAT_RETRY_DEFAULTS)
    return {}


def _provider_for_runner(runner: str, config: dict | None = None) -> str | None:
    alias = _resolve_alias(runner, config)
    if alias is not None:
        provider = alias.get("provider")
        return provider if isinstance(provider, str) and provider else None
    return RUNNER_MODEL_PROVIDERS.get(runner)


def _qualify_model_for_runner(
    runner: str,
    model: str,
    config: dict | None = None,
) -> str:
    provider = _provider_for_runner(runner, config)
    if not provider or not model or "/" in model:
        return model
    return f"{provider}/{model}"


def _split_provider_model(model: str) -> tuple[str | None, str]:
    provider, sep, base_model = model.partition("/")
    if not sep:
        return None, model
    return provider, base_model


def is_copilot_runner(runner: str | None) -> bool:
    """Return True for the base Copilot runner and any 'copilot-<alias>' variant."""
    if not runner:
        return False
    return runner == "copilot" or runner.startswith("copilot-")


def _resolve_alias_model(
    alias: dict[str, Any],
    explicit_model: str | None = None,
) -> str:
    provider = alias.get("provider", "")
    model = explicit_model if explicit_model is not None else alias.get("model", "")
    if provider and model and "/" not in model:
        return f"{provider}/{model}"
    return model or provider


def resolve_runner_transport_config(
    runner: str,
    config: dict | None = None,
) -> dict[str, Any]:
    alias = _resolve_alias(runner, config)
    if alias is None:
        return {}

    transport = _default_transport_for_alias(alias)
    for key in RUNNER_ALIAS_LLM_OPTION_KEYS:
        value = alias.get(key)
        if value is not None:
            transport[key] = value

    api_key_env = alias.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(
                f"runner_aliases[{runner}].api_key_env points to unset environment variable "
                f"'{api_key_env}'"
            )
        transport["api_key"] = api_key

    return transport


def resolve_runner_model(
    runner: str,
    explicit_model: str | None = None,
    config: dict | None = None,
) -> str:
    """Resolve the model string for a given runner (provider or custom alias)."""
    alias = _resolve_alias(runner, config)
    if alias is not None:
        return _resolve_alias_model(alias, explicit_model)

    if runner in RUNNER_DEFAULT_MODELS:
        if explicit_model is not None:
            allowed = RUNNER_MODEL_CHOICES.get(runner, ())
            if allowed and explicit_model not in allowed:
                raise ValueError(
                    f"Model '{explicit_model}' is not valid for runner '{runner}'. "
                    f"Valid models: {', '.join(allowed)}"
                )
            return _qualify_model_for_runner(runner, explicit_model, config)
        return _qualify_model_for_runner(runner, RUNNER_DEFAULT_MODELS[runner], config)

    raise ValueError(
        f"Unknown runner: '{runner}'. Must be a known provider "
        f"({', '.join(sorted(KNOWN_RUNNERS))}) or a custom alias defined in runner_aliases."
    )


def resolve_runner_llm_config(
    runner: str,
    explicit_model: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    llm_config = {"model": resolve_runner_model(runner, explicit_model, config)}
    llm_config.update(resolve_runner_transport_config(runner, config))
    return llm_config


def resolve_agent_model(
    agent_name: str,
    runner: str,
    explicit_model: str | None = None,
    config: dict | None = None,
) -> str:
    """Resolve the model for a given agent and runner."""
    if explicit_model is not None:
        return _qualify_model_for_runner(runner, explicit_model, config)

    if config is not None:
        agent_defaults = config.get("agent_model_defaults", {})
        if agent_name in agent_defaults:
            runner_defaults = agent_defaults[agent_name]
            if isinstance(runner_defaults, dict) and runner in runner_defaults:
                model = runner_defaults[runner]
                if isinstance(model, str):
                    return _qualify_model_for_runner(runner, model, config)

    return resolve_runner_model(runner, None, config)


def resolve_agent_llm_config(
    agent_name: str,
    runner: str,
    explicit_model: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    llm_config = resolve_runner_transport_config(runner, config)
    llm_config["model"] = resolve_agent_model(agent_name, runner, explicit_model, config)
    return llm_config
