"""Provider specifications for the gateway intercept layer.

Each provider maps a gateway path prefix to an upstream URL and defines
which environment variables the runner should set to redirect traffic
through the gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    """Specification for a single provider intercept.

    Attributes:
        name: Short identifier for the provider (e.g. ``"anthropic"``).
        path_prefix: The URL path prefix on the gateway (e.g. ``"/anthropic/"``).
        upstream_url: The real upstream base URL to forward to.
        env_vars: Mapping of environment variable name to the value template.
            Use ``"{gateway_url}"`` as a placeholder for the gateway base URL.
    """
    name: str
    path_prefix: str
    upstream_url: str
    env_vars: dict[str, str] = field(default_factory=dict)

    def resolve_env_vars(self, gateway_url: str) -> dict[str, str]:
        """Return env vars with ``{gateway_url}`` substituted.

        Args:
            gateway_url: The gateway's base URL (e.g. ``"http://127.0.0.1:8765"``).

        Returns:
            Dict of env var name → resolved value.
        """
        return {k: v.format(gateway_url=gateway_url) for k, v in self.env_vars.items()}


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        name="anthropic",
        path_prefix="/anthropic/",
        upstream_url="https://api.anthropic.com",
        env_vars={
            "ANTHROPIC_BASE_URL": "{gateway_url}/anthropic",
        },
    ),
    "openai": ProviderSpec(
        name="openai",
        path_prefix="/openai/",
        upstream_url="https://api.openai.com",
        env_vars={
            # The /v1 suffix follows OpenAI SDK convention for base_url
            "OPENAI_BASE_URL": "{gateway_url}/openai/v1",
        },
    ),
    "azure-devops": ProviderSpec(
        name="azure-devops",
        path_prefix="/azure-devops/",
        upstream_url="https://dev.azure.com",
        env_vars={
            "AZURE_DEVOPS_ORG_URL": "{gateway_url}/azure-devops",
        },
    ),
    "discord": ProviderSpec(
        name="discord",
        path_prefix="/discord/",
        upstream_url="https://discord.com/api",
        env_vars={
            "DISCORD_WEBHOOK_BASE": "{gateway_url}/discord",
        },
    ),
}
