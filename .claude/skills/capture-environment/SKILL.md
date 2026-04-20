---
name: capture-environment
description: Capture the software environment (Python version, pip freeze, conda export, host info) for reproducibility. Use when starting a production run or experiment on Gilbreth.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax so the skill receives the active interpreter and package set up front.

```text
!`python --version`
!`pip freeze`
```

Use the injected values to confirm the environment before persisting the full reproducibility artifacts.

Record the full software environment on the execution host before running the pipeline. These artifacts are required for publication reproducibility.

## Commands (run on Gilbreth before or at job start)

```bash
python --version                              > validation/python_version.txt
pip freeze                                    > validation/pip_freeze.txt
conda env export                              > validation/conda_env.yml   # if using conda
echo "Host: $(hostname), OS: $(uname -a)"    > validation/host_info.txt
date                                         >> validation/host_info.txt
```

The SLURM job script should echo these at the top of the job log automatically:

```bash
echo "Python version: $(python --version)"
echo "Conda env: $CONDA_DEFAULT_ENV"
echo "Node: $SLURM_NODELIST"
echo "Job ID: $SLURM_JOB_ID"
```

## What must be saved

| File | Contents |
|------|----------|
| `validation/python_version.txt` | `python --version` output |
| `validation/pip_freeze.txt` | full `pip freeze` |
| `validation/conda_env.yml` | `conda env export` (if conda) |
| `validation/host_info.txt` | hostname + OS + date |

## Why

Any future attempt to reproduce the run requires knowing the exact package versions. Missing environment artifacts block the reproducibility checklist (`runbooks/REPRODUCIBILITY_CHECKLIST.md`).
