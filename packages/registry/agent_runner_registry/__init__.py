"""Agent Registry — source of truth for versioned agent bundles.

The registry reads bundles from disk under `agent-sources/<name>/<version>/`,
validates manifests against the shared schema, and materializes selected
versions into a target `.claude/agents/` directory at the start of each
run.

Public API:
    load_bundles(sources_dir) -> dict[AgentRef, Bundle]
    resolve(refs, bundles)    -> list[Bundle]
    materialize(bundles, target_dir) -> MaterializationManifest
"""
from __future__ import annotations

from .loader import Bundle, load_bundles
from .resolver import resolve
from .materializer import materialize

__all__ = ["Bundle", "load_bundles", "resolve", "materialize"]
