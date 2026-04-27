# Prefect v3 artifact API reference

This file summarizes the Prefect v3 Python artifact APIs documented under `prefect.artifacts`.

## Artifact behavior rules

- Artifacts are human-readable metadata rendered in Prefect Cloud or Prefect server UI.
- Common use cases include progress tracking, debugging, data quality visibility, and lightweight documentation.
- There are five artifact types: links, Markdown, progress, images, and tables.
- A `key` is required for an artifact to show on the global **Artifacts** page and to accumulate lineage/history.
- Without a `key`, the artifact stays scoped to the associated flow run or task run view.
- Progress artifacts are updated in place instead of versioned.

## Helper functions

| Helper | Async variant | Primary inputs | Returns | Notes |
| --- | --- | --- | --- | --- |
| `create_link_artifact` | `acreate_link_artifact` | `link`, optional `link_text`, optional `key`, optional `description`, optional `client` | `UUID` | Use for external or private resources; readable `link_text` helps operators. |
| `create_markdown_artifact` | `acreate_markdown_artifact` | `markdown`, optional `key`, optional `description` | `UUID` | Best for reports, summaries, status notes, or debugging payloads. |
| `create_table_artifact` | `acreate_table_artifact` | `table`, optional `key`, optional `description` | `UUID` | `table` may be `dict[str, list[Any]]`, `list[dict[str, Any]]`, or `list[list[Any]]`. |
| `create_progress_artifact` | `acreate_progress_artifact` | `progress`, optional `key`, optional `description` | `UUID` | `progress` must be a float from `0` to `100`. |
| `update_progress_artifact` | `aupdate_progress_artifact` | `artifact_id`, `progress`, optional `description`, optional `client` | `UUID` | Updates the existing progress artifact in place. |
| `create_image_artifact` | `acreate_image_artifact` | `image_url`, optional `key`, optional `description` | `UUID` | The image URL must be publicly accessible in the UI. |

## Base class

### `Artifact`

Represents a generic artifact with:

- `type`
- `key`
- `description`
- `data`

Core methods:

| Method family | Purpose |
| --- | --- |
| `create` / `acreate` | Persist an artifact instance |
| `get` / `aget` | Fetch the latest artifact by key |
| `get_or_create` / `aget_or_create` | Idempotently fetch or initialize by key |
| `format` / `aformat` | Convert data into API-ready payload form |

`get_or_create` and `aget_or_create` return `(artifact_response, created_bool)`.

## Typed artifact classes

These subclasses inherit the base create/get/get_or_create lifecycle methods and specialize formatting:

| Class | Typical payload | `format()` output |
| --- | --- | --- |
| `LinkArtifact` | link target plus optional text | `str` |
| `MarkdownArtifact` | Markdown content | `str` |
| `TableArtifact` | tabular data | `str` |
| `ProgressArtifact` | numeric completion percentage | `float` |
| `ImageArtifact` | public image URL | `str` |

## Selection guidance

| If you need... | Use... |
| --- | --- |
| A clickable pointer to data or dashboards | `LinkArtifact` or `create_link_artifact` |
| Rendered prose, checklists, or reports | `MarkdownArtifact` or `create_markdown_artifact` |
| Rows/columns rendered in the UI | `TableArtifact` or `create_table_artifact` |
| A live percentage indicator | `ProgressArtifact`, `create_progress_artifact`, `update_progress_artifact` |
| An image preview in the UI | `ImageArtifact` or `create_image_artifact` |

## Practical notes

- Use link artifacts instead of image artifacts when the image is private or auth-gated.
- Use keyed artifacts to build a durable operator-facing history for the same business object.
- Use `Artifact.get("my_key")` when code needs to read the latest version of an artifact in Python.
- For manual inspection or cleanup outside Python, the docs also mention:
  - `prefect artifact inspect <my_key>`
  - `prefect artifact ls`
  - `prefect artifact delete <my_key>`
  - `prefect artifact delete --id <my_id>`
