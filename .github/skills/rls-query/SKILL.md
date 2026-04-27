---
name: rls-query
description: |
  Use whenever you need repo patterns, existing conventions, file locations, or where-to-change guidance.
  Invokes the Reference Librarian and (if needed) the Information Explorer via separate CLI calls
  to answer knowledge queries without nested subagent violations.
  Keywords: knowledge query, librarian, explorer, codebase patterns, file locations, conventions, where to change, architecture.
---

# RLS Knowledge Query

Query the Reference Librarian (and optionally the Information Explorer) for codebase knowledge, patterns, conventions, and file locations.

## When to Use

Activate this skill when you need:

- Where a feature, service, or component is implemented
- Existing patterns or conventions to follow
- File locations for a domain or layer
- Prior learnings or accumulated knowledge
- Architecture guidance for where to make changes

## Inputs

- `$ARGUMENTS` — The question to answer (required)
- Optional scope hints can be included in the question text (e.g., "in the pearls/sendouts domain")

## How to Invoke

Run the broker script via Bash:

```bash
python3 "$CLAUDE_PROJECT_DIR/.claude/skills/rls-query/scripts/rls_query.py" "$ARGUMENTS"
```

### Optional flags

```bash
python3 "$CLAUDE_PROJECT_DIR/.claude/skills/rls-query/scripts/rls_query.py" "$ARGUMENTS" \
  --change-id "$CHANGE_ID" \
  --repo-root "$CLAUDE_PROJECT_DIR" \
  --artifact-root "$CLAUDE_PROJECT_DIR/agent-context" \
  --timeout 300
```

The script auto-detects repo root, artifact root, and change-id when flags are omitted.

## Outputs

Compact JSON printed to stdout:

```json
{
  "answer": "Short, actionable answer text.",
  "confidence": "full",
  "sources": ["path/to/file1.ts", "agent-context/knowledge/accumulated-knowledge.md"],
  "recommended_next_step": "Proceed with implementation."
}
```

### Confidence levels

| Level     | Meaning                                   |
| --------- | ----------------------------------------- |
| `full`    | Answer is reliable — use directly         |
| `partial` | Answer is likely correct but unverified   |
| `none`    | Could not determine — manual check needed |

## Rules

1. **Do not proceed until you have the broker response.** Wait for the script to complete and read the JSON output.
2. **Do not paste large exploration outputs into the conversation.** The broker stores detailed evidence on disk. Only consume the compact final answer.
3. **Use the answer's confidence level** to decide whether to proceed or verify manually.
4. **Report discoveries back** — if you find new information the librarian didn't know, mention it so future queries benefit.

## How It Works (Internal)

The broker orchestrates three steps as separate top-level CLI calls (no nested subagents):

1. **Librarian query** — Asks the Reference Librarian for an answer with structured JSON output
2. **Explorer search** (only if needed) — If the librarian requires exploration, invokes the Information Explorer in a separate process
3. **Librarian synthesis** — If explorer was called, the librarian synthesizes the final answer from both sources

All detailed evidence is stored under `{artifact_root}/{CHANGE-ID}/knowledge/exploration/`.
All query/response logs are stored under `{artifact_root}/{CHANGE-ID}/logs/`.
