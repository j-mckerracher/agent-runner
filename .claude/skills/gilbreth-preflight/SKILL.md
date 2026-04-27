---
name: gilbreth-preflight
description: Pre-flight checklist for submitting jobs on Gilbreth HPC. Use this before submitting any SLURM job to verify GPU quota, environment, paths, and code version.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax so this skill sees the live Gilbreth queue state instead of relying on the stale quota snapshot alone.

```text
!`ssh jmckerra@gilbreth.rcac.purdue.edu squeue -u jmckerra --noheader`
```

Prefer the injected queue snapshot when deciding whether the account has capacity to start another run.

Before submitting any job to Gilbreth, complete every item below.

## 1. Confirm GPU partition

The `sbagchi` account has **only A100-80GB GPUs allocated** (verified 2026-03-07):

| GPU type       | Quota for `jmckerra` | Account (`sbagchi`) |
|----------------|----------------------|---------------------|
| `hp_a100-80gb` | **2**                | **2**               |
| `hp_a10`       | 0 — do not use       | 0                   |
| `hp_a100-40gb` | 0 — do not use       | 0                   |
| `hp_a30`       | 0 — do not use       | 0                   |
| `hp_h100`      | 0 — do not use       | 0                   |

All SLURM scripts must contain:
```bash
#SBATCH --partition=a100-80gb
#SBATCH --gres=gpu:1
```

Submitting to any other partition will cause the job to queue indefinitely with `AssocGrpGRES`.

### Verified fallback for short non-production jobs

If `a100-80gb` is saturated and you are only running a short development / validation / experiment job, the `sbagchi` account can also submit to:

```bash
#SBATCH --partition=training
#SBATCH --qos=training
#SBATCH --account=sbagchi
#SBATCH --gres=gpu:1
```

Do **not** use `training` for production builds.

## 2. Check current group GPU usage

```bash
slist                        # shows Total/Queued/Running/Free for each GPU type
squeue -A sbagchi            # shows all running/pending jobs in the group
```

The group limit is **2 concurrent A100-80GB GPUs** shared across all `sbagchi` users. If 2 are already running, your job will queue (legitimately) until one finishes.

## 3. Confirm input and output paths exist

```bash
ls /depot/sbagchi/data/josh/FRESCO/chunks/          # source shards
ls /depot/sbagchi/data/josh/FRESCO/chunks-v3/       # output root (create if needed)
```

## 4. Confirm conda environment

```bash
conda activate fresco_v2
python --version
```

## 5. Confirm config file is in place

- Production config: `/home/jmckerra/Code/FRESCO-Pipeline/production_v3.json`
- See `docs/CONFIGURATION.md` for required fields.

## 6. Confirm code is pinned to a commit

```bash
cd /home/jmckerra/Code/FRESCO-Pipeline
git rev-parse HEAD
git status   # should be clean or diffs saved as artifact
```
