---
name: session-logging
description: |
  Agent session logging protocol for multi-agent workflows. Use this skill whenever an agent is spawned and needs to produce a structured log entry. Provides: (1) Standard log file naming convention — {CHANGE-ID}/logs/{agent_name}/{YYYYMMDD_HHMMSS}_session.json, (2) Required log fields — log_type, timestamp, change_id, iteration, session_summary, decisions_made, issues_encountered, notes, (3) Log content requirements — input/output artifacts, librarian queries, key decisions with rationale. Keywords: session log, log entry, agent logging, spawned, log file naming, timestamp, YYYYMMDD, session summary, decisions made, issues encountered, workflow logging.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax when you need a live timestamp for the log filename instead of inventing one manually.

```text
!`date -u +"%Y%m%d_%H%M%S"`
```

Use the injected timestamp directly in the `{YYYYMMDD_HHMMSS}` slot for new log files.

# Agent Session Logging Protocol

Standard logging requirements for all stage agents in multi-agent workflows. The workflow runner follows the same directory convention for its event logs.

## When to Use This Skill

Activate this skill when:

- An agent is spawned/invoked and needs to produce a session log
- Setting up log file paths and naming conventions
- Determining what fields to include in a log entry
- Reviewing log output requirements for an agent

## Core Rule

**Every time an agent is spawned**, it MUST produce a log entry in its designated log directory.

## Log Directory Structure

```
{CHANGE-ID}/logs/{agent_name}/
```

Each agent writes to its own subdirectory:

| Component            | Log Directory                |
| -------------------- | ---------------------------- |
| Workflow Runner      | `logs/workflow_runner/`      |
| Intake Agent         | `logs/intake/`               |
| Reference Librarian  | `logs/reference_librarian/`  |
| Task Generator       | `logs/task_generator/`       |
| Task Assigner        | `logs/assignment/`           |
| Software Engineer    | `execution/{UOW-ID}/logs/`   |
| QA Engineer          | `logs/qa/`                   |
| Information Explorer | `logs/information_explorer/` |
| Lessons Optimizer    | `logs/lessons_optimizer/`    |

## Log File Naming Convention

```
{CHANGE-ID}/logs/{agent_name}/{YYYYMMDD_HHMMSS}_{identifier}.json
```

- **Date format**: `YYYYMMDD` (e.g., `20260127`)
- **Time format**: `HHMMSS` (e.g., `143052`)
- **Identifier**: Describes the log type (e.g., `session`, `query`, `exploration`, `state_transition`)

### Examples

| Component            | Example Filename                        |
| -------------------- | --------------------------------------- |
| Workflow Runner      | `20260127_143000_state_transition.json` |
| Intake Agent         | `20260127_143010_session.json`          |
| Reference Librarian  | `20260127_143052_query.json`            |
| Task Generator       | `20260127_143500_session.json`          |
| Software Engineer    | `20260127_160000_UOW-001_session.json`  |
| QA Engineer          | `20260127_180000_session.json`          |
| Information Explorer | `20260127_143100_exploration.yaml`      |
| Lessons Optimizer    | `20260127_190000_session.json`          |

## Required Log Fields

Every log entry MUST include these fields:

```yaml
log_type: '<agent_type>'
timestamp: '<ISO 8601 timestamp>'
change_id: '<CHANGE-ID>'
iteration: <attempt number>
session_summary:
  input_artifacts_read: ['<list of input files>']
  output_artifacts_written: ['<list of output files>']
  # Additional agent-specific summary fields
```

## Standard Optional Fields

Include these when applicable:

```yaml
reference_librarian_queries:
  - query: '<question asked>'
    confidence: '<full|partial|none>'
    used_in: '<how the answer was used>'
librarian_exploration_summaries_received:
  - original_query: '<question>'
    summary: '<exploration summary>'
decisions_made:
  - decision: '<what was decided>'
    rationale: '<why>'
issues_encountered: []
notes: '<any additional observations>'
```

## Log Content Guidelines

1. **Be specific**: Include exact artifact paths, not vague descriptions
2. **Record reasoning**: Document key decisions and their rationale
3. **Capture issues**: Log any problems encountered, even if resolved
4. **Note observations**: Include anything that might help debug or improve the workflow
5. **Timestamp accurately**: Use ISO 8601 format for timestamps

## Using Logs for Debugging

Logs enable workflow debugging:

1. **Trace execution**: Follow workflow runner logs for stage transitions and retries
2. **Understand decisions**: Each agent logs its reasoning
3. **Debug knowledge flow**: See librarian queries and confidence levels
4. **Track knowledge accumulation**: Follow findings from Explorer → Librarian → accumulated-knowledge.md

---

## Automated Log Initialization Script

Use `{workflow_assets_root}/scripts/init-session-log.py` to create properly named and structured log files:

```bash
{workflow_assets_root}/scripts/init-session-log.py <artifact_root> <change_id> <agent_name> <identifier> [iteration]
```

**Parameters**:

- `agent_name`: one of `workflow_runner`, `intake`, `reference_librarian`, `task_generator`, `assignment`, `software_engineer`, `qa`, `information_explorer`, `lessons_optimizer`
- `identifier`: log type (e.g., `session`, `query`, `state_transition`)
- `iteration`: optional, defaults to 1

**When to use**: At session start. The script creates the log directory, generates the timestamped filename, and writes the JSON stub with all required fields.

**Output**: The created file path to stdout.

**Exit codes**: 0 = success, 1 = failure, 2 = usage error.
