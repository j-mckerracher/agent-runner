"""Runner CLI contract and invocation helpers.

The Runner CLI is the contract between the orchestrator and anything
that drives it (humans, CI, the harness). This module defines the
argument surface and holds contract-level documentation.

Contract version: 1.

Required arguments:
  --workflow <id>             workflow identifier (e.g. "standard")
  --task-spec <path>          path to a task JSON file (or free-form intake)
  --working-copy <path>       fresh, extracted substrate working copy
  --agents-dir <path>         pre-materialized .claude/agents directory
  --artifact-dir <path>       empty directory for runner output
  --event-log <path>          runner appends JSONL events here
  --json-log <path>           per-run structured log

Optional arguments:
  --seed <int>                seed for stochastic components where possible
  --model-config <path>       JSON/YAML file with pinned model identifiers
  --gateway-url <url>         LLM/HTTP traffic target; runner sets env vars
  --dry-run                   synthesize artifacts without invoking LLMs
  --pause-on <stage>          request pause at a named stage

Exit codes:
  0  workflow completed successfully
  2  workflow failed with artifacts written (grade-able)
  3  infra failure; no usable artifacts
  4  cancelled by caller
  *  any other code is an infra bug
"""
from __future__ import annotations

CONTRACT_VERSION = "1"

EXIT_OK = 0
EXIT_WORKFLOW_FAIL = 2
EXIT_INFRA_FAIL = 3
EXIT_CANCELLED = 4
