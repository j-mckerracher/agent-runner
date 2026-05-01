---
name: code-comment-standards
description: |
  Shared standards for story-traceable code comments. Use this skill whenever an agent adds, proposes, or reviews inline or block code comments that reference acceptance criteria, story requirements, tickets, or work items. Requires WI-ID citation for story-linked comments, while leaving ordinary explanatory comments lightweight.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It defines a static commenting standard, so baseline guidance should stay reference-only.

# Code Comment Standards

Shared rules for writing code comments that reference acceptance criteria, story requirements, or work-item context.

## When to Use This Skill

Activate this skill whenever you:

- Add or edit inline code comments in production code, tests, scripts, or generated examples
- Propose comment text in reviews, plans, or implementation guidance
- Explain *why* code exists because of a specific story, ticket, or acceptance criterion
- Write `TODO` or `FIXME` comments tied to a tracked work item

## Core Rule

If a code comment references an acceptance criterion, story requirement, ticket, or work item, it **must include the work item ID**.

**Required format:**

```ts
// WI-XXXXXXX [AC ref if useful]: <explanation>
```

The AC reference is optional unless the comment is tracing a specific acceptance criterion.

## Examples

```ts
// Good: story-specific business rule with work item traceability
status === TestStatus.Revised // WI-4933065 AC1: Disable unreceipt for Revised tests

// Good: ticket-specific maintenance note
// TODO WI-5123456: Remove fallback after legacy API retirement

// Bad: story-specific comment without a work item ID
status === TestStatus.Revised // Disabled per story requirement

// Bad: acceptance criterion reference without traceability
// AC2: Keep the banner visible after reload
```

## Applies To

- Inline comments that explain a condition, branch, or guard tied to a story
- Block comments or JSDoc documenting business rules introduced by a work item
- `TODO`, `FIXME`, or follow-up notes tied to a tracked story or bug
- Code examples included in reviews or agent output when the example cites a story requirement

## Does Not Apply To

You do **not** need to add a work item ID to comments that are purely technical and not story-driven, such as:

- Algorithm explanations
- Type-safety notes
- Concurrency or lifecycle explanations
- Generic readability comments with no ticket/story reference

## Missing Work Item ID Handling

If you believe a story-linked comment is necessary but the work item ID is missing:

1. Check the provided story context, intake artifacts, or prompt for the ID.
2. If the comment can be rewritten as a generic technical explanation, prefer that.
3. If the comment still needs explicit story traceability and the ID is unavailable, ask for the missing story/work item reference before adding the comment.

## Review Rule

When reviewing or revising code comments written by another agent:

- Add the work item ID to any story-linked comment missing it
- Remove vague phrases like `per story`, `per AC`, or `per ticket` unless the comment includes the actual work item ID
- Prefer concise comments; do not add story IDs to comments that are not actually story-specific

## Rationale

This standard makes story-driven code discoverable with commands like `rg 'WI-4933065'`, which helps with audits, reversions, root-cause analysis, and future maintenance.
