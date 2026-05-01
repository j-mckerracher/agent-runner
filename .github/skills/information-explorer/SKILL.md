---
name: information-explorer
description: Protocol for the Reference Librarian to invoke the Information Explorer agent when knowledge confidence is partial. Defines invocation contract, input structure, and expected output. Only the Reference Librarian may use this skill.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is an exploration protocol, so live shell snippets belong in the specific exploration task, not the shared skill body.

# Information Explorer Skill

## Purpose

This skill is used **exclusively by the Reference Librarian** to delegate focused repository/web exploration to the Information Explorer agent when its own knowledge confidence is `partial`.

No other agent may invoke the Information Explorer directly. All knowledge queries flow through the librarian.

## When to Invoke

Invoke the Information Explorer when **all** of the following are true:

1. A knowledge query has been received from a calling agent
2. The librarian's internal knowledge search returned `confidence: partial` (some relevant info found but gaps remain)
3. The gap requires repository traversal, call-chain tracing, or external doc lookup

Do **not** invoke when:
- `confidence: full` — answer directly from knowledge
- `confidence: none` — no exploration will help; add to standing questions instead
- The query is a scoped lessons request — lessons are routed internally, not through the explorer

## How to Invoke

Use the `Agent` tool with `subagent_type: "information-explorer"`.

Construct the prompt using the fields from the `exploration_request`:

```
You are the Information Explorer. Perform focused exploration for the Reference Librarian.

Query: {original query from calling agent}
Hint: {hint — what to trace or locate, e.g. "find where PersonService is defined and its public methods"}
Knowledge mode: {openviking|flat-file}
Report format: File paths + call-chain summary + citations

Write your exploration report to:
  {CHANGE-ID}/logs/information_explorer/{YYYYMMDD_HHMMSS}_exploration.yaml

Return a brief summary of your findings when complete.
```

**Block until the explorer responds** before completing the librarian answer. Do not return a partial answer to the calling agent while exploration is pending.

## Expected Output

The Information Explorer writes its report to:
```
{CHANGE-ID}/logs/information_explorer/{YYYYMMDD_HHMMSS}_exploration.yaml
```

Report structure:
```yaml
answer_summary: "<concise answer to the query>"
confidence: full|partial|none
evidence:
  - type: file|call_chain|external_doc
    reference: "<path or URL>"
    excerpt: "<relevant excerpt>"
key_file_paths:
  - "<path>"
canonical_sources_checked:
  - "<source name or URL>"
unresolved_gaps:
  - "<what could not be found>"
```

After receiving the explorer's response:
1. Ingest the findings into the knowledge system (openviking or flat-file per active mode)
2. Incorporate findings into the final answer to the calling agent
3. Set `explorer_invoked: true` in the librarian log entry

## Restrictions

- The explorer operates read-only. It does not modify source code, artifacts, or knowledge files.
- The librarian — not the explorer — is responsible for writing knowledge updates after exploration.
- If the explorer returns `confidence: none`, escalate the query to standing questions.
