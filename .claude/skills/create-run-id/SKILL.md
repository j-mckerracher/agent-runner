---
name: create-run-id
description: Create a unique run ID and output folder scaffold for FRESCO production runs or experiments. Use this at the very start of any new run before writing any files.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax so the run ID picks up today's date automatically.

```text
!`date +%Y%m%d`
```

Use the injected date in the `PROD-YYYYMMDD-<tag>` pattern instead of typing it manually.

Every production run requires a unique, stable run ID before any files are written.

## Run ID format

```
PROD-YYYYMMDD-<tag>
```

- `YYYYMMDD` — date the run was initiated
- `<tag>` — short descriptor, e.g. `v3`, `rerun`, `anvil-only`

Examples: `PROD-20260203-v3`, `PROD-20260308-rerun`

## Required output folders

Create these before running the pipeline:

```bash
OUTPUT=/depot/sbagchi/data/josh/FRESCO/chunks-v3

mkdir -p $OUTPUT/manifests
mkdir -p $OUTPUT/validation
mkdir -p $OUTPUT/logs
```

Also create a local run folder for reproducibility artifacts (under the pipeline repo or a dedicated research folder):

```
FRESCO-Research/runs/<RUN_ID>/
  config/
  manifests/
  validation/
  logs/
```

## Record the run ID

Write it into `manifests/run_metadata.json` at the start of the run (see the `write-manifests` skill).

## Naming convention for experiments

Experiments (not production runs) use `EXP-XXX` IDs, not `PROD-*`. See `docs/CONFIGURATION.md`.
