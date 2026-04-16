# Agent Workflow Runner

`agent-runner` is the repository's local workflow orchestrator for the staged
custom-agent flow defined in `.claude/agents`.

The public scripts are now intentionally thin:

- `run.py` — interactive workflow launcher
- `run_headless.py` — non-interactive launcher for CI, Discord, and automation
- `run_general.py` — one-shot freeform agent execution helper

Most implementation lives under the internal `agent_runner/` package so future
changes have clear homes instead of accumulating in one large script.

## Workflow order

The runner executes these stages in order:

1. `01-intake`
2. `02-task-generator` + `06-task-plan-evaluator`
3. `03-task-assigner` + `07-assignment-evaluator`
4. `04-software-engineer-hyperagent` + `08-implementation-evaluator`
5. `05-qa` + `09-qa-evaluator`
6. `11-lessons-optimizer-hyperagent`

The runner owns:

- stage ordering and retries
- evaluator feedback loops
- workflow-level logging under `agent-context/<CHANGE-ID>/logs/workflow_runner/`
- structured stdout events for automation consumers
- dry-run artifact synthesis
- pause/resume escalation flow

The intake agent is a stage-local normalizer only. It creates `intake/*`
artifacts but does not orchestrate later stages.

## Internal package layout

`agent_runner/` is organized by responsibility:

| Module area | Purpose |
| --- | --- |
| `models.py` | Shared dataclasses, constants, and workflow config |
| `artifacts.py` | Canonical artifact paths, JSON/text writes, runner log helpers |
| `agents.py` | Agent discovery, backend detection, backend command construction |
| `prompts.py` | Intake, producer, evaluator, and lessons prompt builders |
| `dry_run.py` | Synthetic artifact generation for dry-run mode |
| `runtime.py` | Agent invocation, schema validation, helper-script execution |
| `workflow/stages.py` | Central stage definitions and artifact wiring |
| `workflow/engine.py` | Orchestration loops and workflow execution |
| `integrations/ado.py` | Azure DevOps context fetch and PR creation |
| `integrations/git_worktrees.py` | Worktree lifecycle for headless runs |
| `integrations/discord_resume.py` | Escalation pause/resume handling |
| `integrations/observability.py` | Optional observability sink adapters |
| `cli/interactive.py` | Interactive startup flow and entrypoint |
| `cli/headless.py` | Headless config builder and entrypoint |
| `cli/general.py` | General-purpose runner entrypoint |

## Where future changes should go

- **New workflow rule or stage wiring**: `agent_runner/workflow/stages.py`
- **Workflow control flow / retries / orchestration**: `agent_runner/workflow/engine.py`
- **Prompt wording or prompt assembly**: `agent_runner/prompts.py`
- **Artifact layout or file-path conventions**: `agent_runner/artifacts.py`
- **Backend CLI behavior**: `agent_runner/agents.py`
- **Azure DevOps / Git / Discord / telemetry**: `agent_runner/integrations/`
- **Interactive or headless CLI UX**: `agent_runner/cli/`

This is the main guardrail against the codebase getting messy again: add new
behavior at the seam that owns it instead of reopening the public scripts.

## Startup flows

### Interactive

Run the script with no arguments:

```bash
python3 agent-runner/run.py
```

The interactive launcher will:

1. detect the repo root and artifact root
2. ask which AI backend to use (`copilot` or `claude`)
3. ask how to start the workflow:
   - Azure DevOps work item
   - resume existing intake artifacts
   - paste workflow context manually
4. fetch workflow context from Azure DevOps when given only a work item id or URL
5. skip intake when reusable intake artifacts already exist

### Headless

```bash
python3 agent-runner/run_headless.py --change-id WI-4461550
```

Useful flags:

- `--repo <PATH>`
- `--backend copilot|claude`
- `--output-json <PATH>`
- `--cleanup-worktree`
- `--no-worktree`

### General-purpose run helper

```bash
python3 agent-runner/run_general.py \
  --backend github-copilot \
  --prompt "Fix the failing unit tests" \
  --repo mcs-products-mono-ui
```

## Azure DevOps mode

When you choose Azure DevOps startup, the runner accepts either:

- a bare work item id like `4461550`
- a `WI-4461550` identifier
- a full work item URL like
  `https://dev.azure.com/{org}/{project}/_workitems/edit/{id}`

The runner builds the intake context automatically from the work item title,
description, acceptance criteria, and related metadata.

## Observability

The runner always emits:

- colored console logs
- structured `##EVENT##` lines on stdout
- per-run JSON logs under `agent-context/<CHANGE-ID>/logs/workflow_runner/`

Optional richer observability is available through **Langfuse**. This is
framework-agnostic and fits the runner better than LangChain because the runner
orchestrates external AI CLIs rather than in-process LLM SDK calls.

To enable Langfuse:

1. install the optional Python package: `pip install langfuse`
2. set:
   - `AGENT_RUNNER_OBSERVABILITY=langfuse`
   - `LANGFUSE_PUBLIC_KEY=...`
   - `LANGFUSE_SECRET_KEY=...`
   - optionally `LANGFUSE_HOST=...`

When enabled, the runner records workflow, stage, UoW, agent-dispatch, and
escalation events through the Langfuse adapter while preserving the existing
stdout/file-based contracts.

## Requirements

- Python 3.9+
- one supported AI CLI installed locally:
  - **GitHub Copilot** (`copilot`)
  - **Claude Code** (`claude`)
- for Azure DevOps startup:
  - `az`
  - `azure-devops` CLI extension
  - valid Azure DevOps authentication

Core operation remains standard-library only. Langfuse is optional.

## Tests

```bash
python3 -m unittest agent-runner/test_runner.py -v
python3 -m unittest agent-runner/test_run_headless.py -v
```
