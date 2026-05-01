---
name: capture-code-version
description: Pin a run to a specific git commit hash for reproducibility. Use when starting any production run or experiment to record which version of the pipeline code was used.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax so the skill starts from the live repository state it is about to record.

```text
!`git rev-parse HEAD`
!`git status --short`
```

Treat the injected commit and dirty-state snapshot as the baseline for the validation artifacts this skill writes.

Every run must be pinned to an exact git commit so results are reproducible from source.

## Commands (run on Gilbreth in the pipeline repo)

```bash
cd /home/jmckerra/Code/FRESCO-Pipeline

git rev-parse HEAD              > validation/git_commit.txt
git status --short              > validation/git_status.txt
git diff                        > validation/git_diff.patch   # save if non-empty
```

## Rules

1. **Prefer a clean working tree** — commit or stash all changes before submitting a production job.
2. **If uncommitted diffs exist**, save `git diff` as `validation/git_diff.patch` and note it in `manifests/run_metadata.json`.
3. **Never label a run "publication-ready"** if `git_status.txt` is non-empty and no diff artifact exists.

## What to record in run_metadata.json

```json
{
  "pipeline_git_commit": "<full SHA from git rev-parse HEAD>",
  "pipeline_git_dirty": false
}
```

See the `write-manifests` skill for the full `run_metadata.json` schema.
