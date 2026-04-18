from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .console import log
from .models import (
    AgentSpec,
    BackendSpec,
    BACKEND_SPECS,
    STAGE_AGENT_ALIASES,
    WorkflowConfig,
    WorkflowError,
)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple YAML frontmatter from a markdown file."""

    if not text.startswith("---\n"):
        return {}

    end_index = text.find("\n---\n", 4)
    if end_index == -1:
        return {}

    frontmatter = text[4:end_index]
    result: dict[str, str] = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("'\"")
    return result


def discover_agents(workflow_assets_root: Path) -> dict[str, AgentSpec]:
    """Discover custom agents and index them by file stem and frontmatter name."""

    agents_dir = workflow_assets_root / "agents"
    if not agents_dir.is_dir():
        raise WorkflowError(f"Agent directory not found: {agents_dir}")

    discovered: dict[str, AgentSpec] = {}
    for path in sorted(agents_dir.glob("*.agent.md")):
        text = path.read_text(encoding="utf-8")
        metadata = parse_frontmatter(text)
        key = path.stem.replace(".agent", "")
        name = metadata.get("name", key)
        description = metadata.get("description", "")
        spec = AgentSpec(key=key, name=name, description=description, path=path)
        discovered[key] = spec
        discovered[name] = spec

    unique_keys = {spec.key for spec in discovered.values()}
    log("INFO", f"Discovered {len(unique_keys)} agent(s): {', '.join(sorted(unique_keys))}")
    return discovered


def resolve_agent(agents: dict[str, AgentSpec], stage_key: str) -> AgentSpec:
    """Resolve the preferred agent for a workflow stage."""

    for alias in STAGE_AGENT_ALIASES[stage_key]:
        spec = agents.get(alias)
        if spec is not None:
            return spec
    raise WorkflowError(
        f"Unable to resolve agent for stage '{stage_key}'. "
        f"Looked for: {STAGE_AGENT_ALIASES[stage_key]}"
    )


def detect_available_backends() -> list[BackendSpec]:
    """Return the supported AI backends available on the local machine."""

    import shutil

    available: list[BackendSpec] = []
    for backend in BACKEND_SPECS:
        if shutil.which(backend.command):
            available.append(backend)
    if not available:
        raise WorkflowError(
            "No supported AI CLI was found. Install GitHub Copilot or Claude Code."
        )
    return available


def strip_agent_bullet_prefix(text: str) -> str:
    """Normalize common CLI bullet prefixes from Copilot responses."""

    lines = text.splitlines()
    if len(lines) == 1 and lines[0].startswith("● "):
        return lines[0][2:].strip()
    return text.strip()


def build_agent_command(config: WorkflowConfig, agent: AgentSpec, prompt: str) -> list[str]:
    """Build the backend-specific CLI command for a single agent invocation."""

    seen_roots: set[Path] = set()
    add_dirs: list[str] = []
    roots: Iterable[Path] = [
        config.repo_root,
        config.workflow_assets_root,
        config.artifact_root,
        *config.additional_dirs,
    ]
    for root in roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        add_dirs.extend(["--add-dir", str(root)])

    if config.cli_backend == "copilot":
        command = [
            config.cli_bin,
            "-p",
            prompt,
            "--agent",
            agent.name,
            "--allow-all",
            "--no-ask-user",
            "-s",
            "--stream",
            "off",
            "--output-format",
            "text",
            *add_dirs,
        ]
        if config.model:
            command.extend(["--model", config.model])
        return command

    if config.cli_backend == "claude":
        command = [
            config.cli_bin,
            "--print",
            "--agent",
            agent.name,
            "--output-format",
            "text",
            "--permission-mode",
            "bypassPermissions",
            *add_dirs,
        ]
        if config.model:
            command.extend(["--model", config.model])
        command.extend(
            ["--", prompt]
        )  # -- prevents variadic --add-dir from consuming the prompt
        return command

    raise WorkflowError(f"Unsupported AI backend: {config.cli_backend}")

