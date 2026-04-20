---
name: librarian-query-protocol
description: |
  Reference Librarian query-first workflow for multi-agent systems. Use this skill whenever an agent needs project knowledge, file locations, patterns, prior learnings, scoped lessons, or codebase information. Enforces the mandatory protocol: (1) Query the Reference Librarian FIRST before accessing any knowledge files or doing codebase exploration, (2) Check confidence levels — use 'full' answers directly, wait for explorer on 'partial'/'none', (3) Never access knowledge files directly — all knowledge (including lesson routing) flows through the librarian, (4) Report discoveries back to the librarian for knowledge accumulation. Keywords: reference librarian, query first, knowledge query, confidence level, information explorer, accumulated knowledge, scoped lessons, relevance filtering, codebase patterns, file locations, prior learnings.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. The protocol already defines how to fetch live knowledge, so the base instructions should stay static.

# Reference Librarian Query Protocol

Mandatory workflow for all knowledge queries in multi-agent systems. The Reference Librarian is the single gateway to all project knowledge.

## When to Use This Skill

Activate this skill when:

- An agent needs information about file locations, patterns, or architecture
- An agent encounters unknowns during work
- An agent is starting a new task and needs context
- An agent needs prior learnings or accumulated knowledge
- An agent needs scoped applicable lessons for the current role/stage/task context
- An agent is working on greenfield tasks and needs PRD/plan document information
- An agent discovers new information that should be persisted

## Core Rule

**ALL agents MUST query the Reference Librarian FIRST before accessing any knowledge or doing codebase exploration.**
This includes lesson retrieval; agents should consume scoped `applicable_lessons` instead of reading `agent-context/lessons.md` directly.

Agents do NOT:

- Access knowledge files directly (`agent-context/knowledge/*`)
- Access `agent-context/lessons.md` directly for lesson discovery (except role-specific ownership agents)
- Perform broad exploratory searches for knowledge
- Invoke the Information Explorer directly (only the librarian does this)

## Query-First Workflow

### Step 1: Query the Librarian

When you need information, formulate a specific query and send it to the Reference Librarian.

**Example queries:**

- "What file locations should I know about for this task?"
- "What existing patterns exist for tooltip implementation?"
- "What prior learnings are relevant to database migrations?"
- "What PRD/plan docs define the baseline for this greenfield project?"
- "Where is the PersonService located and what pattern does it follow?"

### Step 2: Check the Confidence Level

The librarian responds with a `confidence` field:

| Confidence | Action                                                                                                                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `full`     | Use the answer directly — it is complete and authoritative                                                                                                                                        |
| `partial`  | The librarian will invoke the Information Explorer to gather more evidence. **You MUST wait** — do not proceed without the complete answer. Re-query the librarian after explorer results arrive. |
| `none`     | Even after exploration, the answer could not be determined. The librarian will add the question to `standing-questions.md`. Check if you can proceed without the information or escalate.         |

### Step 3: Wait for Explorer Results (if partial/none)

**Critical**: When confidence is `partial` or `none`:

1. **Do NOT explore on your own** — do not do broad file searches, directory traversals, or knowledge file reads
2. **Do NOT invoke the Information Explorer directly** — only the librarian does this
3. **Pause and wait** for the librarian to follow up with the complete answer
4. **Re-query the librarian** once explorer results are available

### Step 4: Use the Answer

Once you have a `full` confidence answer:

- Use it to inform your work
- Include it in your output artifacts under `librarian_queries`

## Scoped Lessons Query Contract

When you need prevention rules, ask the librarian for scoped lessons:

```yaml
lesson_request:
  requesting_agent: '<agent-name>'
  workflow_stage: '<stage>'
  task_context:
    summary: '<short summary>'
    keywords: ['<k1>', '<k2>']
    files_in_scope: ['<optional path>']
  max_lessons: 3
```

Expected response:

```yaml
applicable_lessons:
  - lesson_id: '<id>'
    prevention_rule: '<rule>'
    trigger_check: '<check>'
    why_applicable: '<match rationale>'
    confidence: 'high|medium'
omitted_due_to_budget: 0
no_match_reason: null
```

## Reporting Discoveries Back

When you discover useful information during your work (e.g., from implementation, QA validation, or planning), report it back to the librarian so it can be captured in `accumulated-knowledge.md`:

```yaml
report_type: '<agent_type>_findings'
original_query: '<what you originally asked>'
findings:
  summary: '<what you discovered>'
  file_paths: ['<relevant file paths>']
  additional_context: '<source and context>'
```

## Output Artifact Requirements

Every agent that uses this skill must include in their output artifacts:

```yaml
librarian_queries:
  - query: '<the question asked>'
    confidence_received: 'full|partial|none'
    answer_summary: '<brief summary of the answer>'
librarian_exploration_summaries:
  - query: '<the question that needed exploration>'
    summary_received: '<summary of explorer findings>'
applied_lessons:
  - lesson_id: '<id>'
    why_applied: '<brief rationale>'
```
