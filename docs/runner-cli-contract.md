# Runner CLI contract (v1)

The Runner CLI is the sole interface between the orchestrator and its
callers (humans, CI, the harness). This contract is stable across
runner patch releases; breaking changes bump the contract version.

## Required arguments

| Argument | Description |
| --- | --- |
| `--workflow <id>` | Workflow identifier (e.g. `standard`). |
| `--task-spec <path>` | Path to a task JSON file describing the run. |
| `--working-copy <path>` | Fresh, extracted substrate working copy. |
| `--agents-dir <path>` | Pre-materialized `.claude/agents/` directory. |
| `--artifact-dir <path>` | Empty directory for runner output. |
| `--event-log <path>` | Runner appends JSONL events here. |
| `--json-log <path>` | Per-run structured log. |

## Optional arguments

| Argument | Description |
| --- | --- |
| `--seed <int>` | Seed for stochastic components where possible. |
| `--model-config <path>` | JSON/YAML file with pinned model identifiers. |
| `--gateway-url <url>` | LLM/HTTP gateway; runner sets provider base-URL env. |
| `--dry-run` | Synthesize artifacts without invoking LLMs. |
| `--pause-on <stage>` | Request pause at a named stage. |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Workflow completed successfully. |
| `2` | Workflow failed with artifacts written (grade-able). |
| `3` | Infrastructure failure; no usable artifacts. |
| `4` | Cancelled by caller. |
| any other | Runner bug; treat as infra failure. |

## Events

Events are emitted to stdout as `##EVENT## <json>` lines **and** appended
to `--event-log` as JSONL. The line form must remain parseable with the
`event_version: "1"` contract. See `event-contract.md`.

## Backward compatibility

The legacy `run.py`, `run_headless.py`, `run_general.py` entry points
delegate to CLI modules inside `agent_runner.cli.*`. They accept the
historical argument set; the Runner CLI contract above is the forward
interface consumed by the harness.
