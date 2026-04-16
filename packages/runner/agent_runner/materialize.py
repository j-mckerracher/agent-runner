"""Optional registry materialization hook for the runner.

When the runner is invoked inside the harness or CI, the harness owns
materialization. For standalone/interactive use the runner can call
this helper to materialize the workflow's declared agents into
`.claude/agents/` before execution begins. If the registry package is
unavailable this module is a no-op, so the runner degrades gracefully
on installs without the harness dependencies.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .console import log
from .workflow.definition import WorkflowDefinition, load_workflow


def _should_auto_materialize(repo_root: Path, agents_dir: Path | None) -> bool:
    """Return True when auto-detect heuristics say materialization is needed.

    Conditions (all must hold):
    - ``agents_dir`` was NOT explicitly supplied by the caller (harness-managed).
    - ``<repo_root>/agent-sources/`` exists.
    - ``<repo_root>/.claude/agents/`` is absent or empty.
    """
    if agents_dir is not None:
        return False
    sources = repo_root / "agent-sources"
    if not sources.is_dir():
        return False
    agents = repo_root / ".claude" / "agents"
    if agents.is_dir() and any(agents.iterdir()):
        return False
    return True


def maybe_materialize(
    repo_root: Path,
    *,
    agents_dir: Path | None,
    workflow_id: str | None = None,
) -> None:
    """Auto-materialize agents if heuristics say it is needed.

    Safe to call unconditionally — degrades gracefully when:
    - ``agent-sources/`` does not exist.
    - ``.claude/agents/`` is already populated.
    - ``agents_dir`` was explicitly supplied (harness-managed run).
    - The registry package is not importable.
    """
    if not _should_auto_materialize(repo_root, agents_dir):
        return

    wf_id = workflow_id or os.environ.get("AGENT_RUNNER_WORKFLOW", "standard")
    sources_dir = repo_root / "agent-sources"
    target_dir = repo_root / ".claude" / "agents"

    try:
        n = materialize_for_workflow(
            wf_id,
            sources_dir=sources_dir,
            target_dir=target_dir,
        )
        log("INFO", f"materialized {n} agents from agent-sources/")
    except Exception as exc:
        log("WARN", f"auto-materialization skipped: {exc}")


def materialize_for_workflow(
    workflow: WorkflowDefinition | str,
    *,
    sources_dir: Path,
    target_dir: Path,
    extra_refs: Iterable[str] = (),
) -> int:
    """Materialize every agent referenced by `workflow` into `target_dir`.

    Returns the number of bundles materialized. Raises if the registry
    package is unavailable or any ref is unresolved.
    """
    try:
        from agent_runner_registry import load_bundles, materialize, resolve
    except ImportError as exc:
        raise RuntimeError(
            "agent_runner_registry is not importable. Install the workspace "
            "(`pip install -e .`) or run inside the harness-managed env."
        ) from exc

    if isinstance(workflow, str):
        workflow = load_workflow(workflow)

    refs = list(dict.fromkeys([*workflow.agent_refs(), *extra_refs]))
    bundles = load_bundles(Path(sources_dir))
    resolved = resolve(refs, bundles)
    manifest = materialize(resolved, Path(target_dir), clean=True)
    return len(manifest.agents)
