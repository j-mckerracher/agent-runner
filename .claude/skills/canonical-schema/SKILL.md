---
name: canonical-schema
description: FRESCO v3 canonical column schema, required dtypes, and known per-cluster dtype drift. Use when writing extractors, validating output parquet files, adding new columns, or checking schema conformance.
---

Use this skill when writing extractors, checking output parquet files, or adding new columns. Every v3 output shard must conform to this schema.

> **Important**: The actual PROD-20260203-v3 output uses the raw FRESCO column naming convention (e.g., `value_memused`, `queue`). Derived/normalized columns (`runtime_sec`, `peak_memory_gb`, `partition`) are **not stored** — compute them at analysis time. This schema reflects the actual output as of 2026-03-09.

## Required columns

### Identifiers
| Column | Type | Notes |
|---|---|---|
| `jid` | string | Normalized to string across all clusters |
| `jid_global` | string | Globally unique job ID |
| `cluster` | string | Must be one of `{anvil, conte, stampede}` |
| `username` | string | |
| `account` | string | |
| `jobname` | string | |

### Allocations
| Column | Type | Notes |
|---|---|---|
| `nhosts` | int64 | |
| `ncores` | int64 | |
| `timelimit_sec` | float64 | Normalized to seconds |
| `timelimit` | float64 | Raw timelimit value |
| `timelimit_original` | float64 | Original value before normalization |
| `timelimit_unit_original` | string | Unit of original timelimit |
| `queue` | string | Scheduler queue/partition name |
| `host` | string | Submission host |
| `host_list` | string | Allocated nodes |
| `exitcode` | string | |
| `unit` | string | |

### Timing
| Column | Type | Notes |
|---|---|---|
| `time` | timestamp[us] | Main measurement timestamp |
| `submit_time` | timestamp[us] | |
| `start_time` | timestamp[us] | |
| `end_time` | timestamp[us] | |

> `runtime_sec`, `queue_time_sec`, `runtime_fraction` are **not stored** — derive as needed:
> `runtime_sec = (end_time - start_time).dt.total_seconds()`

### Performance metrics (raw FRESCO values)
| Column | Type | Notes |
|---|---|---|
| `value_cpuuser` | float64 | CPU user time |
| `value_gpu` | float64 | GPU utilization |
| `value_memused` | float64 | Memory used (may include cache) |
| `value_memused_minus_diskcache` | float64 | Memory minus disk cache |
| `value_nfs` | float64 | NFS I/O |
| `value_block` | float64 | Block I/O |

> `peak_memory_gb`, `peak_memory_fraction`, `node_memory_gb` are **not stored** in v3 output.

### Provenance (measurement semantics)
| Column | Type | Purpose |
|---|---|---|
| `memory_includes_cache` | bool | True if `value_memused` includes disk cache |
| `memory_collection_method` | string | e.g., `procfs`, `cgroup`, `scheduler` |
| `memory_aggregation` | string | e.g., `max`, `mean` |
| `memory_sampling_interval_sec` | float64 | How often memory was sampled |

## Dtype drift: known per-cluster issues

All columns in PROD-20260203-v3 output are stored as `timestamp[us]` (not `timestamp[ns]`). The pipeline normalizes to `timestamp[us]` across all clusters.

| Column | Anvil (raw) | Conte (raw) | Stampede (raw) | Fix |
|---|---|---|---|---|
| `ncores` | int64 | double | int32 | Cast to int64 before write |
| `nhosts` | int64 | double | int32 | Cast to int64 before write |
| timestamps | varies | varies | `timestamp[us]` | Normalize to `timestamp[us]` |
| `exitcode` | string | string | dict-encoded string | Decode to plain string |
| `timelimit` | double | double | int64 | Cast to float64 |

## Rules for extractor authors

1. **Always cast explicitly** — never rely on pandas/pyarrow to infer dtypes during parquet write.
2. **Missing columns must be present as nulls** — use schema union-by-name; do not drop columns that are absent for one cluster.
3. **No object-dtype columns** in parquet output — convert to string or numeric before write.
4. **`cluster` must be populated** for every row — never leave it null.
5. **Timestamps must be timezone-aware** — normalize to UTC before write.

## Versioning

Any addition of new canonical columns requires a schema version bump in `run_metadata.json`. Existing columns must remain backward-compatible (rename = breaking change; add nullable column = non-breaking).
