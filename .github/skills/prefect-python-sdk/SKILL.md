---
name: prefect-python-sdk
description: |
  Guide for using Prefect v3 Python artifact APIs from flows and tasks. Use this skill when writing, reviewing, or debugging Python code that creates, updates, or reads Prefect artifacts with `prefect.artifacts`. Keywords: prefect, python sdk, prefect.artifacts, artifact, create_markdown_artifact, create_table_artifact, create_progress_artifact, update_progress_artifact, create_link_artifact, create_image_artifact, Artifact.get, get_or_create, async artifacts
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is a static API reference and usage guide, so live project state should be gathered only during a concrete Prefect task.

# Prefect Python SDK: Artifacts

Use this skill for workflow-visible metadata and human-readable outputs in Prefect v3. Prefer the top-level helper functions for straightforward publishing from flows and tasks, and use the artifact classes when you need explicit object construction or idempotent `get_or_create` behavior.

## When to Use This Skill

Activate this skill when:

- Writing Python code that publishes artifacts to the Prefect UI
- Choosing between link, markdown, table, progress, and image artifacts
- Reading an existing artifact by key in Python
- Updating a progress artifact over time
- Deciding whether to use sync or async artifact APIs
- Debugging why an artifact does or does not appear in the Artifacts page

## Core Rules

- Use a `key` when the artifact should appear on the global **Artifacts** page or maintain history across runs.
- Keys must contain only lowercase letters, numbers, and dashes.
- If no `key` is provided, the artifact is visible only on the associated flow run or task run.
- Progress artifacts are updated in place with `update_progress_artifact`; keyed link, markdown, table, and image artifacts create lineage over time.
- `create_image_artifact` requires a publicly accessible image URL. If the asset is private, publish a link artifact instead.
- Match sync and async APIs to the calling context: `create_*` / `update_*` in sync code, `acreate_*` / `aupdate_*` in async code.
- If your local Prefect package differs from the v3 docs, verify the installed version before assuming every helper exists unchanged.

## Quick Chooser

| Need | Preferred API | Notes |
| --- | --- | --- |
| Publish a clickable URL | `create_link_artifact` | Use `link_text` to improve readability. |
| Publish formatted narrative/report output | `create_markdown_artifact` | Great for summaries, runbooks, and diagnostics. |
| Publish tabular data | `create_table_artifact` | Accepts dict-of-lists, list-of-dicts, or list-of-lists. |
| Show percent-complete progress | `create_progress_artifact` | Initial value must be a float from 0 to 100. |
| Update an existing progress indicator | `update_progress_artifact` | Store the returned artifact id from creation time. |
| Render a public image in the UI | `create_image_artifact` | Use only for publicly reachable image URLs. |
| Read the latest artifact version by key | `Artifact.get` | Returns `None` when the key is not found. |
| Fetch or initialize a keyed artifact | `Artifact.get_or_create` | Returns `(artifact_response, created_bool)`. |

## Recommended Usage Patterns

### Publish artifacts from flows or tasks

- Use artifacts for outputs humans should inspect in the Prefect UI.
- Keep the artifact `key` stable when you want an evolving history for the same logical artifact.
- Prefer descriptive Markdown in `description` when operators need context around the data.

### Track long-running work

- Call `create_progress_artifact(progress=0.0, ...)` once.
- Save the returned artifact id.
- Call `update_progress_artifact(artifact_id=..., progress=...)` as work advances.

### Read or initialize artifacts in Python

- Use `Artifact.get(key)` when the artifact should already exist.
- Use `Artifact.get_or_create(key=..., description=..., data=...)` for idempotent initialization.
- Use the typed classes when the artifact type itself is part of the design intent.

## Reference Files in This Skill

- `artifact-reference.md` — complete helper/class surface and behavior notes from the Prefect v3 artifact API reference.
