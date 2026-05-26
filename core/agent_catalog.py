from __future__ import annotations

# Agents that remain in source history but must not be materialized, listed as
# active, resolved for model defaults, or invoked by the workflow.
DISABLED_AGENTS: frozenset[str] = frozenset({
    "lessons-optimizer-hyperagent",
})


def is_disabled_agent(name: str | None) -> bool:
    return bool(name) and str(name) in DISABLED_AGENTS
