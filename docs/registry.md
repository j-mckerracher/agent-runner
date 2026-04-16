# Agent Registry

The Agent Registry (`packages/registry`) is the source of truth for versioned
agent definitions. It decouples agent prompts and configuration from the runner
and from Claude Code's fixed `.claude/agents/` location expectation.

For the architectural motivation, see [01-broad-architecture.md](01-broad-architecture.md)
§9.2 and §4 ("Materialization over location coupling").

---

## 1. Bundle layout

Each agent version is an **immutable bundle** stored under `agent-sources/`:

```
agent-sources/
  <name>/
    <version>/
      manifest.yaml          — required: bundle metadata
      prompt.md              — agent prompt (the content of the .agent.md file)
      tools.json             — optional: tool list
      config.yaml            — optional: extra agent config
```

Example: `agent-sources/intake/v1/manifest.yaml`

```yaml
name: intake
version: v1
description: "intake agent, v1, seeded from .claude/agents/01-intake.agent.md"
source: "agent-development/.claude/agents/01-intake.agent.md"
claude_code_agent_file: "01-intake.agent.md"
```

The directory name (`<name>/<version>`) is used as a fallback if the manifest
omits `name` or `version`, but the manifest values take precedence.

---

## 2. Manifest fields

Defined by `packages/shared/agent_runner_shared/schemas/agent_manifest.schema.json`.

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Agent identifier; lowercase hyphenated. |
| `version` | Yes | Version string (e.g. `v1`, `1.2.0`). |
| `description` | No | Human-readable description. |
| `source` | No | Origin of the bundle (git path, URL, or filename). |
| `claude_code_agent_file` | No | Filename to write in `.claude/agents/`. Defaults to `<name>.agent.md`. |
| `tools` | No | List of tool identifiers this agent expects. |
| `requires_tools` | No | If true, the harness will verify required tools are available. |
| `model_recommendations` | No | Suggested model identifiers for this agent. |

---

## 3. Agent refs

An agent ref is a `name@version` string uniquely identifying one bundle:

```
intake@v1
software-engineer-hyperagent@v1
task-generator@v1
```

Refs appear in:
- `task.yaml` under `agents:`.
- Workflow stage definitions under `agent:`, `producer:`, `evaluator:`.
- The `--agents` flag of `agent-runner-registry materialize`.

---

## 4. Materialization semantics

Materialization writes selected bundles into a target directory (typically
`.claude/agents/` inside a run's working copy). The operation is **destructive**:
if the target directory already exists, it is wiped before writing (to prevent
stale agents from leaking between runs). Explicitly pass `clean=False` to
`materialize()` to suppress this behaviour.

For each bundle:
1. The `prompt.md` text is written to `<target_dir>/<claude_code_agent_file>`.
2. A SHA-256 content hash is computed and stored in the manifest.
3. If `prompt.md` is absent or empty, a placeholder stub is written.

After all bundles are written, a `.materialization.json` file is written into
the target directory:

```json
{
  "agents": [{"name": "intake", "version": "v1"}, ...],
  "target_dir": "/path/to/.claude/agents",
  "content_hashes": {"intake@v1": "abc123...", ...},
  "materialized_at": "2026-04-16T18:00:00Z"
}
```

This file is the authoritative record of which bundles were materialized for a
run. The harness reads it when building the `RunLineage` header.

---

## 5. CLI

```
agent-runner-registry materialize --sources <DIR> --target <DIR> [--agents REF ...]
```

| Flag | Default | Description |
|---|---|---|
| `--sources` | `agent-sources` | Root of the bundle library. |
| `--target` | (required) | Target directory (`.claude/agents/` or similar). |
| `--agents` | (all) | Specific agent refs to materialize; omit to materialize all. |

**Examples:**

```bash
# Materialize all agents into the default location for a dev run
agent-runner-registry materialize --target .claude/agents

# Materialize only intake for a targeted run
agent-runner-registry materialize --target .claude/agents --agents intake@v1

# Materialize into a custom path (e.g. inside a container working copy)
agent-runner-registry materialize \
  --sources /repo/agent-sources \
  --target /run/working-copy/.claude/agents \
  --agents intake@v1 task-generator@v1
```

The CLI is also available as a Python module:
```bash
PYTHONPATH=packages/shared:packages/registry \
  python -m agent_runner_registry.cli materialize --target .claude/agents
```

---

## 6. Relationship to `.claude/agents/`

Claude Code requires agent prompts to be in `.claude/agents/` at a fixed path
relative to the repository root it operates on. The registry is the **source of
truth**; `.claude/agents/` is a **derived artifact** written fresh at the start
of every run.

Do not commit hand-edited files to `.claude/agents/` as a source of truth. Make
changes in `agent-sources/<name>/<version>/prompt.md` and bump the version.
The runner's `--no-materialize` flag bypasses registry materialization for
quick local tests where you want to hand-edit `.claude/agents/` directly; this
is flagged as non-authoritative in the lineage.

---

## 7. Python API

```python
from agent_runner_registry import load_bundles, resolve, materialize
from pathlib import Path

# Load all bundles from agent-sources/
bundles = load_bundles(Path("agent-sources"))

# Resolve specific refs (raises LookupError on miss)
selected = resolve(["intake@v1", "task-generator@v1"], bundles)

# Materialize into a target directory
manifest = materialize(selected, Path(".claude/agents"))
print(f"Materialized {len(manifest.agents)} agents")
```

---

## 8. Versioning guidance

- **Immutability:** once a bundle is committed under `agent-sources/<name>/<version>/`,
  do not modify its files. Create a new version (`v2`, etc.) instead.
- **Version strings:** use `v1`, `v2`, etc. for major revisions. Semantic versioning
  (`1.0.0`, `1.1.0`) is supported but not required.
- **Workflow refs:** update the workflow YAML to reference the new version; this is
  a workflow-level change and triggers a new workflow version.
- **Task corpus:** tasks pin agent versions explicitly in their `agents:` list. A
  task's agent list must be updated (and the task re-calibrated) when the agents
  it needs change versions.

---

## 9. Adding a new agent version

1. Create `agent-sources/<name>/<new-version>/`.
2. Write `manifest.yaml` with the new version string.
3. Write `prompt.md` with the full agent prompt.
4. Update any workflow definitions that should use the new version.
5. Update any task corpus entries that pin this agent.
6. Re-calibrate affected tasks (see [docs/calibration.md](calibration.md)).
7. Commit the bundle and the updated workflow/tasks together.
