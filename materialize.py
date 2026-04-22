"""
materialize.py — Build script for agent prompt materialization.

Reads every agent-sources/{name}/v{n}/manifest.yaml + prompt.md and writes
the prompt to .claude/agents/{claude_code_agent_file}, then updates
.claude/agents/.materialization.json with content hashes and a timestamp.

Usage:
    python materialize.py               # materialize all agents (latest version)
    python materialize.py --check       # check for drift only, exit 1 if stale
    python materialize.py --agent intake --agent task-generator  # specific agents only
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

RUNNER_ROOT = Path(__file__).resolve().parent
AGENT_SOURCES_ROOT = RUNNER_ROOT / "agent-sources"
AGENTS_DIR = RUNNER_ROOT / ".claude" / "agents"
MATERIALIZATION_FILE = AGENTS_DIR / ".materialization.json"


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
    return versions[-1] if versions else None


def load_manifest(manifest_path: Path) -> dict:
    with manifest_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_materialization() -> dict:
    if MATERIALIZATION_FILE.exists():
        with MATERIALIZATION_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "agents": [],
        "target_dir": str(AGENTS_DIR),
        "content_hashes": {},
        "materialized_at": None,
    }


def save_materialization(data: dict) -> None:
    MATERIALIZATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MATERIALIZATION_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# ── Core logic ────────────────────────────────────────────────────────────────

def discover_agents(filter_names: list[str] | None = None) -> list[dict]:
    """
    Walk agent-sources/ and return a list of agent descriptors:
      { name, version, prompt_path, target_filename }
    """
    agents = []
    for agent_dir in sorted(AGENT_SOURCES_ROOT.iterdir()):
        if not agent_dir.is_dir():
            continue
        version = latest_version(agent_dir)
        if not version:
            continue
        manifest_path = agent_dir / version / "manifest.yaml"
        prompt_path = agent_dir / version / "prompt.md"
        if not manifest_path.exists() or not prompt_path.exists():
            print(f"  [WARN] Skipping {agent_dir.name}/{version} — missing manifest or prompt.md")
            continue
        manifest = load_manifest(manifest_path)
        name = manifest.get("name") or agent_dir.name
        if filter_names and name not in filter_names and agent_dir.name not in filter_names:
            continue
        agents.append({
            "name": name,
            "source_name": agent_dir.name,
            "version": version,
            "prompt_path": prompt_path,
            "target_filename": manifest["claude_code_agent_file"],
        })
    return agents


def materialize(agents: list[dict], check_only: bool = False) -> bool:
    """
    Copy prompt.md → .claude/agents/{target_filename} for each agent.
    If check_only=True, only report drift without writing.
    Returns True if everything is up-to-date (or was successfully materialized).
    """
    current = load_materialization()
    existing_hashes: dict[str, str] = current.get("content_hashes", {})

    new_agents_list = []
    new_hashes = {}
    drift_detected = False
    all_clean = True

    for agent in agents:
        key = f"{agent['name']}@{agent['version']}"
        target_path = AGENTS_DIR / agent["target_filename"]
        prompt_hash = sha256_of_file(agent["prompt_path"])
        new_hashes[key] = prompt_hash

        existing_hash = existing_hashes.get(key)
        target_exists = target_path.exists()
        target_hash = sha256_of_file(target_path) if target_exists else None

        is_stale = (
            not target_exists
            or prompt_hash != existing_hash
            or prompt_hash != target_hash
        )

        if is_stale:
            drift_detected = True
            all_clean = False
            if check_only:
                print(f"  [DRIFT]  {key} → {agent['target_filename']}")
            else:
                AGENTS_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(agent["prompt_path"], target_path)
                print(f"  [WRITE]  {key} → {agent['target_filename']}")
        else:
            print(f"  [OK]     {key} → {agent['target_filename']}")

        new_agents_list.append({"name": agent["name"], "version": agent["version"]})

    if not check_only and drift_detected:
        current["agents"] = new_agents_list
        current["target_dir"] = str(AGENTS_DIR)
        current["content_hashes"] = {**existing_hashes, **new_hashes}
        current["materialized_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_materialization(current)
        print(f"\n  Materialization complete. Updated {MATERIALIZATION_FILE.relative_to(RUNNER_ROOT)}")
        all_clean = True

    return all_clean or not drift_detected


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize agent prompts into .claude/agents/")
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
    Returns True if all agents are up-to-date after the operation.
    """
    print("Discovering agents in agent-sources/...")
    agents = discover_agents(filter_names)
    if not agents:
        print("  No agents found.")
        return True
    print(f"  Found {len(agents)} agent(s).\n")
    label = "Checking" if check_only else "Materializing"
    print(f"{label} agents into {AGENTS_DIR.relative_to(RUNNER_ROOT)}/...")
    return materialize(agents, check_only=check_only)


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

