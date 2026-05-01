---
name: skill-authoring
description: |
  Guide for creating or updating GitHub Copilot agent skills. Use when asked to create, revise, review, or install a reusable skill with a SKILL.md file, companion resources, or scripts. Covers choosing when a skill is the right customization type, writing effective frontmatter and instructions, placing project skills in the correct directory, and validating the result.
---

# Skill Authoring

Create or update GitHub Copilot agent skills using the GitHub skill format and this repository's local conventions.

## When to Use

Activate this skill when:

- Creating a new agent skill
- Refactoring repeated task guidance into a reusable skill
- Reviewing an existing skill for correctness, clarity, or discoverability
- Adding templates, examples, or scripts to a skill directory

## Decide Whether This Should Be a Skill

Use a skill when the behavior is task-specific, reusable, and detailed enough that it should only be loaded when relevant.

Prefer other customization types when they fit better:

- Use custom instructions for always-on repository rules
- Use prompt files for one-off reusable prompts with fill-in inputs
- Use custom agents for long-lived specialist roles or constrained tool access

## Project Skill Placement

GitHub supports project skills in `.github/skills`, `.claude/skills`, or `.agents/skills`.

In this repository, add live project skills under:

```text
.claude/skills/<skill-name>/
```

Rules:

- Use a lowercase, hyphenated folder name
- Match the folder name to the frontmatter `name`
- Always create a `SKILL.md` file
- Keep companion files inside the same skill directory
- Only mirror the skill elsewhere if explicitly requested

## Required Skill Structure

At minimum, create:

```text
.claude/skills/<skill-name>/
`-- SKILL.md
```

`SKILL.md` must contain YAML frontmatter and a Markdown body.

Required frontmatter:

- `name`: unique lowercase, hyphenated identifier
- `description`: what the skill does and when Copilot should use it

Optional frontmatter:

- `license`
- `allowed-tools`

Only add `allowed-tools` when it is truly necessary. Do not pre-approve `bash` or `shell` unless the skill's scripts are trusted and have been reviewed.

## Authoring Workflow

1. Pick a concise, hyphenated skill name.
2. Confirm a skill is the right customization mechanism for the task.
3. Create `.claude/skills/<skill-name>/SKILL.md`.
4. Write a strong `description` that helps Copilot auto-select the skill when relevant.
5. Organize the body around when to use the skill, the expected inputs, the process to follow, and any rules or constraints.
6. Add companion resources only if they materially improve reuse.
7. If scripts are needed, store them in the same skill directory and reference them explicitly from `SKILL.md`.
8. If the skill is added during an active CLI session, reload and inspect it with `/skills reload` and `/skills info <skill-name>`.

## Repository Conventions

When authoring skills for this repository:

- Follow the direct protocol style used by existing files in `.claude/skills/*/SKILL.md`
- Add a `## Dynamic context` section only when the skill actually needs runtime injection guidance
- Keep the instructions narrowly scoped to the task the skill is meant to improve
- Prefer the smallest useful set of companion files
- If the new skill becomes part of the shared workflow prompt set, update `.claude/agents/README.md` and `agents/README.md`; otherwise leave those inventories unchanged

## Starter Template

Use `templates/basic-skill-template.md` as the starting point for new skills.

## Completion Checklist

Before finishing a new or updated skill, confirm that:

- The directory name and frontmatter `name` match
- `SKILL.md` clearly explains when the skill should be used
- The body includes a concrete workflow or rules, not just a description
- Any companion files are colocated in the skill directory and referenced explicitly
- Any `allowed-tools` entry is intentional and safe
- The skill can be reloaded and inspected in the CLI if needed

## Example Outcome

When asked to create a new repository skill:

1. Decide whether the request belongs in a skill, custom instructions, a prompt file, or a custom agent.
2. Create `.claude/skills/<skill-name>/`.
3. Start from `templates/basic-skill-template.md`.
4. Tailor the frontmatter and instructions to the requested workflow.
5. Add only the supporting files that the skill truly needs.
