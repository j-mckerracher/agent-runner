"""
materialize.py — Build script for agent + skill materialization.

Reads canonical sources from:
  - agent-definition-source/{name}/v{n}/manifest.yaml + prompt.md
  - agent-skill-source/{name}/v{n}/manifest.yaml + SKILL.md
  - agent-script-source/**/*

Then writes runner-specific generated artifacts for Claude, Copilot, and Gemini
into their respective directories and records content hashes + timestamps in a
per-runner `.materialization.json` file.

Usage:
    python materialize.py               # materialize all agents/skills/scripts
    python materialize.py --check       # check for drift only, exit 1 if stale
    python materialize.py --agent intake --agent task-generator  # specific agents only
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.materialized_paths import (
    RUNNER_AGENT_DIRS,
    RUNNER_METADATA_FILES,
    RUNNER_ROOT,
    RUNNER_SCRIPT_DIRS,
    RUNNER_SKILL_DIRS,
)

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

AGENT_SOURCES_ROOT: Path = RUNNER_ROOT / "agent-definition-source"
SKILL_SOURCES_ROOT: Path = RUNNER_ROOT / "agent-skill-source"
SCRIPT_SOURCES_ROOT: Path = RUNNER_ROOT / "agent-script-source"


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def latest_version(agent_dir: Path) -> str | None:
    """Return the highest vN directory name inside an agent source folder."""
    versions = sorted(
        [d.name for d in agent_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
        key=lambda v: int(v[1:]),
    )
    result = versions[-1] if versions else None
    logger.debug("latest_version: %s → %s", agent_dir.name, result)
    return result


def load_manifest(manifest_path: Path) -> dict:
    logger.debug("load_manifest: %s", manifest_path)
    with manifest_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_materialization(metadata_file: Path) -> dict:
    logger.debug("load_materialization: checking %s", metadata_file)
    if metadata_file.exists():
        with metadata_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug(
            "load_materialization: loaded %d agent(s), %d skill(s), %d script(s)",
            len(data.get("agents", [])),
            len(data.get("skills", [])),
            len(data.get("scripts", [])),
        )
        return data
    logger.debug("load_materialization: file absent; returning empty state")
    return {
        "agents": [],
        "skills": [],
        "scripts": [],
        "target_dir": str(metadata_file.parent),
        "content_hashes": {},
        "materialized_at": None,
    }


def save_materialization(metadata_file: Path, data: dict) -> None:
    logger.debug("save_materialization: writing to %s", metadata_file)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    with metadata_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    logger.debug("save_materialization: written OK")


# ── Core logic ────────────────────────────────────────────────────────────────

def _agent_target_filename(manifest: dict, runner: str) -> str:
    if runner == "claude":
        return manifest["claude_code_agent_file"]
    if runner == "copilot":
        return (
            manifest.get("github_copilot_agent_file")
            or manifest.get("copilot_agent_file")
            or manifest["claude_code_agent_file"]
        )
    if runner == "gemini":
        return manifest.get("gemini_agent_file") or manifest["claude_code_agent_file"]
    raise ValueError(f"Unsupported runner for agent target filename: {runner}")


def _skill_target_filename(manifest: dict, runner: str) -> str:
    if runner == "claude":
        return manifest.get("claude_skill_file") or manifest.get("skill_file") or "SKILL.md"
    if runner == "copilot":
        return (
            manifest.get("github_copilot_skill_file")
            or manifest.get("copilot_skill_file")
            or manifest.get("skill_file")
            or "SKILL.md"
        )
    if runner == "gemini":
        return manifest.get("gemini_skill_file") or manifest.get("skill_file") or "SKILL.md"
    raise ValueError(f"Unsupported runner for skill target filename: {runner}")


def discover_agents(filter_names: list[str] | None = None) -> list[dict]:
    """
    Walk agent-definition-source/ and return a list of agent descriptors:
      { name, version, prompt_path, target_filename }
    """
    agent_sources_root = cast(Path, AGENT_SOURCES_ROOT)
    agents = []
    agent_dirs: list[Path] = list(agent_sources_root.iterdir())
    agent_dirs.sort(key=lambda path: path.name)
    for agent_dir in agent_dirs:
        if not agent_dir.is_dir():
            continue
        version = latest_version(agent_dir)
        if not version:
            logger.debug("discover_agents: %s has no versions; skipping", agent_dir.name)
            continue
        manifest_path = agent_dir / version / "manifest.yaml"
        prompt_path = agent_dir / version / "prompt.md"
        if not manifest_path.exists() or not prompt_path.exists():
            logger.warning("discover_agents: skipping %s/%s — missing manifest or prompt.md", agent_dir.name, version)
            print(f"  [WARN] Skipping {agent_dir.name}/{version} — missing manifest or prompt.md")
            continue
        manifest = load_manifest(manifest_path)
        name = manifest.get("name") or agent_dir.name
        if filter_names and name not in filter_names and agent_dir.name not in filter_names:
            logger.debug("discover_agents: %s not in filter; skipping", name)
            continue
        agents.append({
            "kind": "agent",
            "name": name,
            "source_name": agent_dir.name,
            "version": version,
            "source_path": prompt_path,
            "runner_targets": {
                runner: _agent_target_filename(manifest, runner)
                for runner in RUNNER_AGENT_DIRS
            },
        })
        logger.debug("discover_agents: found agent name=%s version=%s", name, version)
    logger.info("discover_agents: %d agent(s) discovered", len(agents))
    return agents


def discover_skills(filter_names: list[str] | None = None) -> list[dict]:
    """Walk agent-skill-source/ and return a list of skill descriptors."""
    skill_sources_root = cast(Path, SKILL_SOURCES_ROOT)
    logger.debug("discover_skills: scanning %s filter=%s", str(skill_sources_root), filter_names)
    if not skill_sources_root.exists():
        logger.info("discover_skills: source root is absent; returning empty list")
        return []

    skills = []
    skill_dirs: list[Path] = list(skill_sources_root.iterdir())
    skill_dirs.sort(key=lambda path: path.name)
    for skill_dir in skill_dirs:
        if not skill_dir.is_dir():
            continue
        version = latest_version(skill_dir)
        if not version:
            logger.debug("discover_skills: %s has no versions; skipping", skill_dir.name)
            continue
        manifest_path = skill_dir / version / "manifest.yaml"
        skill_path = skill_dir / version / "SKILL.md"
        if not manifest_path.exists() or not skill_path.exists():
            logger.warning("discover_skills: skipping %s/%s — missing manifest or SKILL.md", skill_dir.name, version)
            print(f"  [WARN] Skipping {skill_dir.name}/{version} — missing manifest or SKILL.md")
            continue
        manifest = load_manifest(manifest_path)
        name = manifest.get("name") or skill_dir.name
        if filter_names and name not in filter_names and skill_dir.name not in filter_names:
            logger.debug("discover_skills: %s not in filter; skipping", name)
            continue
        skills.append({
            "kind": "skill",
            "name": name,
            "source_name": skill_dir.name,
            "version": version,
            "source_path": skill_path,
            "runner_targets": {
                runner: f"{name}/{_skill_target_filename(manifest, runner)}"
                for runner in RUNNER_SKILL_DIRS
            },
        })
        logger.debug("discover_skills: found skill name=%s version=%s", name, version)
    logger.info("discover_skills: %d skill(s) discovered", len(skills))
    return skills


def discover_scripts(filter_names: list[str] | None = None) -> list[dict]:
    """Walk agent-script-source/ and return a list of script descriptors."""
    script_sources_root = cast(Path, SCRIPT_SOURCES_ROOT)
    logger.debug("discover_scripts: scanning %s filter=%s", str(script_sources_root), filter_names)
    if not script_sources_root.exists():
        logger.info("discover_scripts: source root is absent; returning empty list")
        return []

    scripts = []
    script_files = [
        path for path in sorted(script_sources_root.rglob("*"), key=lambda path: str(path.relative_to(script_sources_root)))
        if path.is_file()
    ]
    for script_path in script_files:
        relative_path = script_path.relative_to(SCRIPT_SOURCES_ROOT)
        script_name = relative_path.as_posix()
        if filter_names and script_name not in filter_names and script_path.stem not in filter_names:
            logger.debug("discover_scripts: %s not in filter; skipping", script_name)
            continue
        scripts.append({
            "kind": "script",
            "name": script_name,
            "source_name": script_name,
            "version": "source",
            "source_path": script_path,
            "runner_targets": {
                runner: script_name
                for runner in RUNNER_SCRIPT_DIRS
            },
        })
        logger.debug("discover_scripts: found script path=%s", script_name)
    logger.info("discover_scripts: %d script(s) discovered", len(scripts))
    return scripts


def _target_dir_for_item(item: dict, runner: str) -> Path:
    if item["kind"] == "agent":
        return RUNNER_AGENT_DIRS[runner]
    if item["kind"] == "skill":
        return RUNNER_SKILL_DIRS[runner]
    return RUNNER_SCRIPT_DIRS[runner]


def materialize(agents: list[dict], skills: list[dict], scripts: list[dict], check_only: bool = False) -> bool:
    """
    Copy canonical agent + skill + script source files into all runner-specific generated
    directories. If check_only=True, only report drift without writing.

    Returns True if everything is up-to-date (or was successfully materialized).
    """
    items = [*agents, *skills, *scripts]
    logger.info(
        "materialize: %d agent(s), %d skill(s), %d script(s) check_only=%s",
        len(agents),
        len(skills),
        len(scripts),
        check_only,
    )
    any_drift = False

    for runner, metadata_file in RUNNER_METADATA_FILES.items():
        current = load_materialization(metadata_file)
        existing_hashes: dict[str, str] = current.get("content_hashes", {})
        new_hashes: dict[str, str] = {}
        new_agents_list: list[dict] = []
        new_skills_list: list[dict] = []
        new_scripts_list: list[dict] = []
        runner_drift = False

        for item in items:
            key = f"{item['kind']}:{item['name']}@{item['version']}"
            target_dir = _target_dir_for_item(item, runner)
            target_path = target_dir / item["runner_targets"][runner]
            source_hash = sha256_of_file(item["source_path"])
            new_hashes[key] = source_hash

            existing_hash = existing_hashes.get(key)
            target_exists = target_path.exists()
            target_hash = sha256_of_file(target_path) if target_exists else None
            is_stale = not target_exists or source_hash != existing_hash or source_hash != target_hash

            if item["kind"] == "agent":
                new_agents_list.append({"name": item["name"], "version": item["version"]})
            elif item["kind"] == "skill":
                new_skills_list.append({"name": item["name"], "version": item["version"]})
            else:
                new_scripts_list.append({"name": item["name"], "version": item["version"]})

            display_target = target_path.relative_to(RUNNER_ROOT)
            if is_stale:
                any_drift = True
                runner_drift = True
                if check_only:
                    logger.warning("materialize: DRIFT runner=%s %s → %s", runner, key, display_target)
                    print(f"  [DRIFT]  {runner}:{key} → {display_target}")
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item["source_path"], target_path)
                    logger.info("materialize: WRITE runner=%s %s → %s", runner, key, display_target)
                    print(f"  [WRITE]  {runner}:{key} → {display_target}")
            else:
                logger.debug("materialize: OK runner=%s %s", runner, key)
                print(f"  [OK]     {runner}:{key} → {display_target}")

        if not check_only and runner_drift:
            current["agents"] = new_agents_list
            current["skills"] = new_skills_list
            current["scripts"] = new_scripts_list
            current["target_dir"] = str(metadata_file.parent)
            current["content_hashes"] = {**existing_hashes, **new_hashes}
            current["materialized_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            save_materialization(metadata_file, current)
            logger.info("materialize: complete for runner=%s, updated %s", runner, metadata_file)
            print(f"\n  Materialization complete. Updated {metadata_file.relative_to(RUNNER_ROOT)}")

    return not any_drift or not check_only


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize agents, skills, and scripts for Claude, Copilot, and Gemini.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for drift only. Exits with code 1 if any agent is stale.",
    )
    parser.add_argument(
        "--agent",
        dest="agents",
        action="append",
        metavar="NAME",
        help="Materialize only the named agent(s). Can be repeated.",
    )
    return parser.parse_args()


def run_materialization(filter_names: list[str] | None = None, check_only: bool = False) -> bool:
    """
    Importable entry point used by run.py.
    Returns True if all runner-specific generated artifacts are up-to-date after
    the operation.
    """
    print("Discovering agents in agent-definition-source/, skills in agent-skill-source/, and scripts in agent-script-source/...")
    agents = discover_agents(filter_names)
    skills = discover_skills(filter_names)
    scripts = discover_scripts(filter_names)
    if not agents and not skills and not scripts:
        print("  No agents, skills, or scripts found.")
        return True
    print(f"  Found {len(agents)} agent(s), {len(skills)} skill(s), and {len(scripts)} script(s).\n")
    label = "Checking" if check_only else "Materializing"
    print(f"{label} runner artifacts into .claude/, .github/, and .gemini/...")
    logger.info(
        "run_materialization: %d agent(s), %d skill(s), %d script(s) check_only=%s",
        len(agents),
        len(skills),
        len(scripts),
        check_only,
    )
    result = materialize(agents, skills, scripts, check_only=check_only)
    logger.info("run_materialization: complete, all generated artifacts up-to-date=%s", result)
    return result


if __name__ == "__main__":
    args = parse_args()
    ok = run_materialization(filter_names=args.agents, check_only=args.check)
    if args.check and not ok:
        print("\n[FAIL] One or more agents are stale. Run `python materialize.py` to update.")
        sys.exit(1)
    elif not ok:
        print("\n[FAIL] Materialization encountered errors.")
        sys.exit(1)
    else:
        if args.check:
            print("\n[OK] All agents are up-to-date.")
        sys.exit(0)

