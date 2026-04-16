# Rebaseline Procedure

Baselines encode the *expected* pass-rate band for a task under a specific
judge configuration. When that configuration changes, the baseline becomes
stale and must be regenerated ("rebaselined") before comparative analysis
is meaningful.

---

## When Rebaseline Is Required

A rebaseline is required whenever **any** of the following change:

| Trigger | Field that signals staleness |
|---|---|
| Judge model version bump | `BaselineBand.judge_model` |
| Prompt version bump (`PROMPT_VERSION`) | `BaselineBand.reason` (by convention, embed `prompt_v=<N>`) |
| Task definition version bump | `BaselineBand.task_version` |
| Substrate commit change | `BaselineBand.reason` (by convention, embed `substrate=<sha>`) |

> **Note:** Bumping `PROMPT_VERSION` in
> `packages/harness/agent_runner_harness/grading/prompts/__init__.py`
> is the canonical signal that all rubric-graded baselines are invalid.

---

## Procedure

### 1. Identify Affected Tasks

Run the baseline check against the current configuration to list tasks
whose stored baseline differs from the current judge model or prompt
version:

```sh
agent-runner-harness baseline check --baselines-dir baselines/
```

This prints a table of tasks with `[STALE]` or `[OK]` status.
You can also check against a specific git ref:

```sh
agent-runner-harness baseline check --baselines-dir baselines/ --against main
```

### 2. Regenerate Each Affected Band

For each stale task, run at least 10 calibration runs and record the
reason so the baseline file is self-documenting:

```sh
agent-runner-harness calibrate \
    --task <task_id> \
    --k 10 \
    --baselines-dir baselines/ \
    --reason "judge_model=gpt-5.4-high prompt_v=2" \
    --judge-stub   # remove this flag in production
```

Repeat for every stale task.

### 3. Verify Band Width

After calibration, inspect the new band:

```sh
agent-runner-harness baseline show --task <task_id> --baselines-dir baselines/
```

A healthy band has:
- `sample_size >= 5`
- `high - low <= 0.40` (wide bands indicate unstable tasks)
- `mean` consistent with prior runs (large shifts warrant investigation)

### 4. Commit the Updated Baselines

```sh
git add baselines/<task_id>.json
git commit -m "rebaseline: <task_id> — <reason>

Judge model: gpt-5.4-high
Prompt version: 2
Sample size: 10

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

The `reason` field inside `baselines/<task_id>.json` MUST be populated
and MUST include enough context to understand why the rebaseline happened.

### 5. Announce

Post in the team channel with:
- Which tasks were rebaselined
- The trigger (model bump / prompt bump / task version bump)
- A link to the commit

---

## `baseline check` Command

```
agent-runner-harness baseline check [--baselines-dir DIR] [--against GIT_REF]
```

Reads every `*.json` file in `baselines/` and compares:
- `judge_model` against the current default judge model
  (`gpt-5.4-high` unless overridden with `--judge-model`)
- `prompt_version` embedded in `reason` against `PROMPT_VERSION`
  from the grading prompts module

Exits with code **0** if all baselines are current, **1** if any are stale.

---

## `calibrate --reason` Flag

```
agent-runner-harness calibrate --task <id> --k <N> --reason "<string>"
```

The `--reason` string is stored verbatim in `BaselineBand.reason`. By
convention include the judge model and prompt version:

```
--reason "judge_model=gpt-5.4-high prompt_v=1 substrate=abc1234"
```

---

## FAQ

**Q: Can I rebaseline without running the full harness?**  
A: No. The band must be derived from actual judge calls under the current
configuration. Do not hand-edit baseline files.

**Q: What if the band shifts dramatically?**  
A: Investigate the task first. A sudden mean shift often means the task
definition changed or the substrate diverged. Fix the root cause before
committing a new band.

**Q: How many runs (`--k`) should I use?**  
A: 10 is the minimum for a reliable band. Use 20 for high-stakes tasks or
tasks with high variance (wide bands).
