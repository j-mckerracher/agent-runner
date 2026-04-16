# Substrates Manifest

The `substrates.yaml` file maps **substrate refs** to pinned repository snapshots
used as the working environment for evaluation runs.

## Format

```yaml
version: 1
substrates:
  <ref-name>:
    description: "Human-readable description"
    repo_url: "URL or local file:// path to the git repository"
    commit: "Git commit SHA or HEAD"
```

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Manifest schema version (must be `1`) |
| `substrates` | map | Mapping of ref-name → substrate entry |
| `ref-name` | string | Unique identifier used in task.yaml `substrate.ref` |
| `description` | string | Human-readable description of this substrate |
| `repo_url` | string | Git URL or `file://` path for the substrate repo |
| `commit` | string | Git SHA to pin, or `HEAD` for latest |

## Usage

Tasks reference substrates via `substrate.ref` in their `task.yaml`:

```yaml
substrate:
  ref: baseline-2026-04-16
```

The harness resolves the ref at run time, checking out the specified commit
into a fresh working copy before invoking the runner.

## Adding a New Substrate

1. Commit (or tag) the repo state you want to capture.
2. Add an entry to `substrates.yaml` with the commit SHA.
3. Reference the new ref in any tasks that should use it.

## Notes

- Use `HEAD` only for the seed/development substrate. For reproducible
  calibration runs, always pin to an explicit commit SHA.
- `file://` URLs reference the local filesystem and are suitable for
  development. Production runs should use HTTPS git URLs.
