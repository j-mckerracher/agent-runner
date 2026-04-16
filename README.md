# Agent Runner Monorepo

Agent Runner is an experimental-apparatus for measuring the impact of
changes to agents, prompts, tools, and workflows driving staged
agentic software delivery. It is organized as a **monorepo** of three
cooperating subsystems plus a shared core.

## Repository layout

```
agent-runner/
├── bin/                         thin CLI shims
├── packages/
│   ├── shared/                  agent_runner_shared — types, schemas, event contract
│   ├── runner/                  agent_runner        — orchestrator (Runner CLI)
│   ├── registry/                agent_runner_registry — versioned agent bundles
│   └── harness/                 agent_runner_harness  — evaluation harness
├── agent-sources/               source of truth for agent bundles (name/version)
├── task-corpus/                 versioned evaluable tasks (dual-format ACs)
├── substrates/                  pinned "test repositories" manifest
├── cassettes/                   recorded LLM/HTTP traffic for replay
├── baselines/                   per-task pass-rate bands
├── runs/                        run archives (tiered retention)
├── docker/                      Dockerfile + container assets
├── tools/                       dev tooling (ac-migrator, etc.)
├── tests/                       cross-package tests
└── docs/                        architecture + usage documentation
```

## Backward-compatible entrypoints

The three original scripts still work from the repo root:

- `python3 run.py` — interactive workflow launcher
- `python3 run_headless.py --change-id WI-XXXX ...` — non-interactive
- `python3 run_general.py --backend ... --prompt ... --repo ...` — freeform

Equivalent invocations via the packaged workspace entry points:

- `agent-runner --change-id WI-XXXX ...`
- `agent-runner-interactive`
- `agent-runner-general --backend ... --prompt ...`
- `agent-runner-registry list`
- `agent-runner-harness evaluate --task <id> --dev-mode`

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Development

```
# Run all tests
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  pytest

# Lint import boundaries
lint-imports -c pyproject.toml
```

## Architecture

See `docs/01-broad-architecture.md` and `docs/02-detailed-architecture.md`
(copies of the documents under `../agent-development/agent-infra/`) for
the full design intent. Key principles:

1. Agents are **source-of-truth versioned** in `agent-sources/`, not
   inside `.claude/agents/`. The registry materializes bundles into
   `.claude/agents/` at run start.
2. Workflows are **data** (`packages/runner/agent_runner/workflows/*.yaml`),
   not code; the imperative engine drives them.
3. Runs are **hermetic** — container + pinned substrate + cassette
   playback + pinned models and seeds.
4. Evaluation is a **first-class subsystem** (the harness) owning
   corpora, grading, baselines, and regression detection.
5. Judgments use a **different model** than generation: pinned
   `gpt-5.4-high`. Any change to the judge is a full rebaseline event.

## License

Internal / private.
