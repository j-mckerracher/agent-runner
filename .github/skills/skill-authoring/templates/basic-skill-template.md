# Basic Skill Template

Use this as a starting point for a new project skill.

## Suggested directory layout

```text
.claude/skills/<skill-name>/
|-- SKILL.md
`-- <optional companion files>
```

## Suggested `SKILL.md`

```md
---
name: your-skill-name
description: |
  Short description of what the skill does and when Copilot should use it.
# Optional:
# license: MIT
# allowed-tools: bash
---

# Human-readable title

One sentence that explains the skill's purpose.

## When to Use

- Situation 1
- Situation 2

## Inputs

- Any required arguments, paths, IDs, or assumptions

## Workflow

1. Step one
2. Step two
3. Step three

## Rules

- Important constraint
- Safety rule
- Repository-specific convention

## Validation

- How to confirm the skill was added correctly
```

## Notes

- Keep the directory name and frontmatter `name` identical.
- Omit `allowed-tools` unless the skill truly needs it.
- If the skill uses scripts or reference files, keep them in the same skill directory and name them clearly.
