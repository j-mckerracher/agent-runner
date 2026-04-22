# agent-runner

This workflow runner can execute against either a live Azure DevOps work item URL or a local synthetic story fixture for workflow testing.

## Synthetic local testing

Bundled fixtures:

| File | Change ID | Difficulty | Description |
|------|-----------|------------|-------------|
| `agent-context/test-fixtures/synthetic_story.json` | `TEST-AC-001` | Simple | Meta smoke-test validating the workflow itself (no real domain) |
| `agent-context/test-fixtures/synthetic_story_medium.json` | `TEST-MEDIUM-001` | Medium | RLS Send-Outs barcode validation — real domain story requiring codebase discovery and multi-task decomposition |

Run with the bundled synthetic story:

```zsh
python run.py --repo /absolute/path/to/target/repo
```

Run with a specific synthetic story fixture:

```zsh
python run.py --repo /absolute/path/to/target/repo --story-file /absolute/path/to/story.json
```

Optional change id override:

```zsh
python run.py --repo /absolute/path/to/target/repo --story-file /absolute/path/to/story.json --change-id TEST-AC-999
```

## Live Azure DevOps intake

```zsh
python run.py --repo /absolute/path/to/target/repo --ado-url 'https://dev.azure.com/<org>/<project>/_workitems/edit/123456'
```

If the work item id cannot be inferred from the URL, also pass `--change-id`.

## Synthetic fixture format

The synthetic story file must be JSON and include:

- `change_id` (or pass `--change-id`)
- `title`
- `description`
- `acceptance_criteria`

`acceptance_criteria` may be either:

- a non-empty list of strings, or
- a non-empty object map such as `{ "AC1": "...", "AC2": "..." }`

Optional fields such as `examples`, `constraints`, `non_functional_requirements`, and ADO metadata may also be included.

## Local validation

```zsh
python -m unittest discover -s tests -v
python run.py --help
```
