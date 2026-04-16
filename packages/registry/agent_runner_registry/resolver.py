"""Resolves agent refs (strings or AgentRef objects) against a bundle set."""
from __future__ import annotations

from typing import Iterable

from agent_runner_shared.models import AgentRef

from .loader import Bundle


def resolve(refs: Iterable[str | AgentRef], bundles: dict[AgentRef, Bundle]) -> list[Bundle]:
    result: list[Bundle] = []
    missing: list[str] = []
    for raw in refs:
        ref = AgentRef.parse(raw) if isinstance(raw, str) else raw
        bundle = bundles.get(ref)
        if bundle is None:
            missing.append(str(ref))
            continue
        result.append(bundle)
    if missing:
        raise LookupError(f"Agent refs not found in registry: {', '.join(missing)}")
    return result
