---
name: validate-outputs
description: Validate FRESCO pipeline output parquet files against quality levels 0–3. Use after any production pipeline run or experiment completes to confirm outputs meet acceptance criteria before archiving or making claims.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax to show which validation reports already exist before you decide what still needs to be generated or checked.

```text
!`ls validation/*.json 2>/dev/null | xargs -I{} basename {}`
```

Treat the injected artifact list as the current inventory of validation outputs.

After the production pipeline runs, validation artifacts must be produced and checked before the run is considered complete.

## Required validation artifacts

| File | Contents |
|------|----------|
| `validation/schema_report.json` | Column names + dtypes per cluster, union vs intersection |
| `validation/missingness_report.json` | Per-column missingness % per cluster |
| `validation/dtype_report.json` | Dtype per column per cluster + any drift flags |
| `validation/sanity_checks.json` | Row counts, date range coverage, cluster distribution |

These are produced automatically by `build_production_v3.py` when `--config` includes `"write_validation_reports": true`.

## Validation levels

### Level 0 — Schema (hard stop if fails)
- All required canonical columns are present (see `canonical-schema` skill)
- Dtypes match expected types — no object columns where numeric is intended

### Level 1 — Sanity (hard stop if fails)
- `nhosts > 0`, `ncores > 0`, `timelimit_sec > 0`
- `runtime_sec >= 0`, `queue_time_sec >= 0`
- `peak_memory_fraction` in `(0, 2]` after cleaning (document cleaning threshold in config)

### Level 2 — Cross-field consistency (quantify and document; do not hard-stop)
- `runtime_sec <= timelimit_sec * (1 + epsilon)` — allow scheduler rounding
- `runtime_fraction` in `[0, 1 + epsilon]`
- Log any violations as a count + fraction in `validation/sanity_checks.json`

### Level 3 — Distribution monitoring (advisory; not a hard-stop unless extreme)
- Missingness per column per cluster per month
- Drift checks across months (flag large month-over-month swings)
- Use for operational awareness, not as a production gate

## Acceptance criteria

| Level | Required to pass? |
|---|---|
| 0 | **Yes** — hard stop |
| 1 | **Yes** — hard stop |
| 2 | Document failures; do not stop unless >5% of rows fail |
| 3 | Monitor only |

## Stop conditions — abort the run if any of these occur

- Any **dtype mismatch** during parquet append or write
- **Schema drift** not handled by union-by-name
- **Validation Level 0 or Level 1 fails**
- `cluster` column is missing or contains values outside `{anvil, conte, stampede}`

## Key invariants to check

1. All output parquet files contain the `cluster` column with values in `{anvil, conte, stampede}`.
2. No dtype instability: `ncores`, `nhosts`, `timelimit` must be cast to canonical types before write.
3. Missing columns must be present as nulls — not absent.
4. Timestamps must be normalized to a single timezone/resolution.

See `docs/SCHEMA_AND_PROVENANCE.md` and `docs/DATA_QUALITY_AND_VALIDATION.md` for full rules.

## Checking validation output

```bash
cat /depot/sbagchi/data/josh/FRESCO/chunks-v3/validation/schema_report.json | python -m json.tool
cat /depot/sbagchi/data/josh/FRESCO/chunks-v3/validation/missingness_report.json | python -m json.tool
```
