# AC Migrator

Converts Azure DevOps (ADO) work item JSON payloads into task corpus entries
compatible with the Agent Runner evaluation harness.

## Usage

```bash
# Interactive mode: classify each AC as deterministic or rubric
python -m tools.ac_migrator.cli --from-ado payload.json --out task-corpus/my-task

# Non-interactive: make all criteria rubric by default
python -m tools.ac_migrator.cli --from-ado payload.json --out task-corpus/my-task --yes
```

## Arguments

| Argument | Description |
|----------|-------------|
| `--from-ado PAYLOAD.json` | Path to the ADO work item JSON payload |
| `--out TASK_DIR` | Output directory for the task corpus entry |
| `--yes` | Non-interactive: all criteria become rubric |

## What It Does

1. **Parses** the ADO payload JSON (from ADO REST API or similar export).
2. **Extracts** acceptance criteria from:
   - `System.Description` HTML field (scans for "Acceptance Criteria" section)
   - `Microsoft.VSTS.Common.AcceptanceCriteria` HTML field
3. **Classifies** each criterion (interactive) or defaults to rubric (`--yes`).
4. **Writes** `task.yaml` with proper dual-format structure.
5. **Generates** stub Python scripts for deterministic criteria.

## ADO Payload Format

The tool expects a JSON file structured like the ADO REST API response:

```json
{
  "id": 1234,
  "fields": {
    "System.Title": "My Work Item",
    "System.Description": "<p>Description with <h2>Acceptance Criteria</h2><ul>...</ul></p>",
    "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>...</li></ul>"
  }
}
```

## Interactive Classification

For each extracted AC candidate, you'll be prompted:

```
[AC 1] The system must export all records to CSV

  (d) deterministic  (r) rubric  (s) skip
  Choice [d/r/s]:
```

- `d` — Creates a stub Python script you fill in to check the output.
- `r` — Creates a rubric criterion with a judge LLM prompt.
- `s` — Skips this criterion entirely.

## Output Structure

```
task-corpus/<task-id>/
  task.yaml
  criteria/
    deterministic/
      check_1.py
      check_2.py
```

After generation, review and edit:
- `task.yaml` — fill in agents, models, substrate ref, inputs
- `criteria/deterministic/check_N.py` — implement the actual checks
