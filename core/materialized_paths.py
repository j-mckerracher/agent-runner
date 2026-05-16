from __future__ import annotations

from pathlib import Path

from .runner_models import KNOWN_RUNNERS


def _canonical_runner(runner: str) -> str:
    """Map a runner (standard or custom alias) to its canonical provider root."""
    if runner in KNOWN_RUNNERS:
        return runner
    try:
        from server.config import load_config
        cfg = load_config()
        alias = (cfg.get("runner_aliases") or {}).get(runner)
        if alias and alias.get("provider") in RUNNER_OUTPUT_ROOTS:
            return alias["provider"]
    except Exception:
        pass
    return "claude"

RUNNER_ROOT = Path(__file__).resolve().parent.parent

RUNNER_OUTPUT_ROOTS: dict[str, Path] = {
    "claude": RUNNER_ROOT / ".claude",
    "copilot": RUNNER_ROOT / ".github",
    "gemini": RUNNER_ROOT / ".gemini",
}

RUNNER_AGENT_DIRS: dict[str, Path] = {
    runner: root / "agents"
    for runner, root in RUNNER_OUTPUT_ROOTS.items()
}

RUNNER_SKILL_DIRS: dict[str, Path] = {
    runner: root / "skills"
    for runner, root in RUNNER_OUTPUT_ROOTS.items()
}

RUNNER_SCRIPT_DIRS: dict[str, Path] = {
    runner: root / "scripts"
    for runner, root in RUNNER_OUTPUT_ROOTS.items()
}

RUNNER_METADATA_FILES: dict[str, Path] = {
    runner: root / ".materialization.json"
    for runner, root in RUNNER_OUTPUT_ROOTS.items()
}


def normalize_runner(runner: str) -> str:
    """Map runner aliases to the canonical materialized asset root."""
    return _canonical_runner(runner)


def runner_agent_dir(runner: str) -> Path:
    return RUNNER_AGENT_DIRS[normalize_runner(runner)]


def runner_skill_dir(runner: str) -> Path:
    return RUNNER_SKILL_DIRS[normalize_runner(runner)]


def runner_script_dir(runner: str) -> Path:
    return RUNNER_SCRIPT_DIRS[normalize_runner(runner)]


def runner_metadata_file(runner: str) -> Path:
    return RUNNER_METADATA_FILES[normalize_runner(runner)]

