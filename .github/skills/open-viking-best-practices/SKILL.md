---
name: open-viking-best-practices
description: >
  Best practices for using OpenViking (ov) as a context database for AI agents.
  Use this skill whenever you are about to search for information, store knowledge,
  read a file, browse a codebase, or manage context — to make sure you are using
  ov in the right way, in the right order, for the right job. This skill covers
  WHEN to use ov, WHAT to store where, HOW to read progressively to save tokens,
  WHICH retrieval method to choose, and HOW to grow ov's memory over time.
  Consult this whenever you're not sure whether to use ov, or how best to use it.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is best-practices guidance, so keep the shared skill stable and inject live context only per task.

# OpenViking Best Practices

OpenViking (`ov`) is a context database organized as a virtual file system under the
`viking://` URI scheme. It unifies three types of context — **Resources** (knowledge),
**Memory** (state), and **Skills** (capabilities) — and provides layered, on-demand
loading to keep token costs low while keeping retrieval accurate.

The CLAUDE.md for this project enforces an **OpenViking-First Policy**: reach for `ov`
before doing ad-hoc file reads, grep searches, or prompt-stuffing. The commands
themselves are documented in the `open-viking-cli` skill; this skill explains the
*why* and *when* behind them.

---

## 1. What goes where

The `viking://` namespace has three top-level paths. Choose the right one so retrieval
works cleanly.

| Path | Context Type | What belongs here | Lifecycle |
|------|-------------|-------------------|-----------|
| `viking://resources/` | Resource | Project docs, code repos, READMEs, web pages, API specs, FAQs, reference material | Long-term, relatively static |
| `viking://user/memories/` | Memory (user) | User preferences, habits, entity records (people, projects), event logs, decisions | Long-term, dynamically updated |
| `viking://agent/memories/` | Memory (agent) | Learned cases, task patterns, tool usage experience | Long-term, grows with use |
| `viking://agent/skills/` | Skill | Callable capabilities, tool definitions, MCPs | Long-term, static |

**Rule of thumb:** if a human wrote it and you'll look it up by topic → Resource.
If it describes who the user is or what happened → Memory. If it's something an agent
*does* → Skill.

---

## 2. Read progressively — never jump straight to L2

Every piece of content in ov has three layers generated automatically on ingestion:

| Layer | Command | Token budget | When to use |
|-------|---------|-------------|-------------|
| L0 Abstract | `ov abstract <uri>` | ~100 tokens | Quick triage — is this the right resource? |
| L1 Overview | `ov overview <uri>` | ~2k tokens | Planning and decision-making — enough to act on most questions |
| L2 Detail | `ov read <uri>` | Unlimited | When you actually need the full text |

**Always start at L0, escalate only when needed.** The L0 abstract is automatically
returned in search results (`ctx.abstract`), so you often have it for free. Reading L1
before L2 prevents loading large files you don't need.

```
# Good: progressive drill-down
ov abstract viking://resources/my-project/
ov overview viking://resources/my-project/      # only if abstract wasn't enough
ov read viking://resources/my-project/api.md    # only if you need the raw file
```

---

## 3. Choose the right retrieval method

| Situation | Use | Why |
|-----------|-----|-----|
| You know the path | `ov ls` / `ov tree` | Deterministic, no vector cost |
| Simple keyword lookup | `ov grep <pattern>` | Exact text match, fast |
| Semantic question, known scope | `ov find <query> --target-uri <uri>` | Vector search scoped to one project |
| Broad semantic question | `ov find <query>` | Searches across all context types |
| Conversational search (you have prior context) | `ov search <query>` | Intent analysis + query expansion using session context |
| Exploring an unfamiliar area | `ov tree` then `ov abstract` on promising nodes | Navigate structure before committing to search |

**`find` vs `search`:** `find` is direct vector similarity — fast, deterministic, good
for targeted lookups. `search` runs intent analysis and query expansion first, making
it more powerful but slower; use it when the query is ambiguous or conversational.

**Scope your searches.** An unscoped `ov find` searches everything. Prefer:
```
ov find "error handling patterns" --target-uri viking://resources/my-project/
```
over a global search when you already know which project is relevant. Scoped searches
return higher-quality results and cost fewer tokens.

**Use specific queries.** `ov find "OAuth 2.0 authorization code flow"` beats
`ov find "auth"` every time. The embedding model rewards specificity.

---

## 4. When to ingest into ov

Ingest content into ov when it is:
- Documentation you will look up again (project README, API spec, design doc)
- A skill or capability definition you want available across sessions
- A web page or external reference you're likely to revisit
- Anything too large to fit comfortably in a prompt

Ingest it **as soon as you discover it**, not when you're rushed. Ingestion triggers
async processing (`ov add-resource` / `ov add-skill`); if you need to search it
immediately after, run `ov wait-processed` first.

```
ov add-resource ./docs/            # ingest a local directory
ov add-resource https://...        # ingest a URL
ov add-skill ./skills/my-skill/    # ingest a skill definition
ov wait-processed                  # block until indexing is done before searching
```

Do **not** ingest ephemeral content (scratch pads, one-off notes, things you won't
query again). ov is a knowledge base, not a log file.

---

## 5. Grow memory through sessions

Memory in ov is not just static storage — it self-evolves through session commits.

At the end of meaningful work (a conversation, a task, a debugging session), trigger
memory extraction:
```
ov session commit
```

This asynchronously analyzes what happened and updates:
- `viking://user/memories/profile` — user identity and background
- `viking://user/memories/preferences` — preferences by topic
- `viking://user/memories/entities` — people, projects, recurring subjects
- `viking://user/memories/events` — decisions and milestones
- `viking://agent/memories/cases` — specific learned cases
- `viking://agent/memories/patterns` — generalizable patterns

**Commit after non-trivial sessions.** Skipping commits means the agent doesn't learn.
Over time, committed sessions make retrieval dramatically more accurate because the
memory reflects actual usage patterns.

---

## 6. Organize resources with hierarchy

ov's recursive retrieval works *with* your directory structure. A flat dump of files
under `viking://resources/` is harder to search than a well-organized tree:

```
viking://resources/
├── my-project/           # group by project or domain
│   ├── api-spec/
│   ├── architecture/
│   └── runbooks/
├── company-wide/         # shared org knowledge
│   ├── engineering-guide/
│   └── onboarding/
└── external-refs/        # third-party docs you've ingested
    └── azure-devops/
```

The retrieval engine uses directory structure to improve precision: it first locates
high-scoring directories, then explores within them. Good hierarchy means better
recall without needing to increase the result limit.

---

## 7. Follow-up with relations

After a search hit, ov can tell you what else is related. This is particularly useful
when a single resource is part of a larger web of connected content (e.g., an API spec
that references its auth guide, or a runbook that links to its deployment checklist).

```
ov relations <uri>
```

Returns related resources with a reason explaining the connection. Use it when:
- You found a partial answer and want to see if there's a more complete sibling
- You want to explore a topic without doing a second full search
- The first result is a directory node and you want to know what's adjacent to it

Relations are a lightweight way to navigate the graph without another vector search.

---

## 8. Embedded vs Service mode — which to use

| Mode | How to start | When to use |
|------|-------------|-------------|
| **Embedded** | `ov.OpenViking(path="./data")` — local storage, singleton | Development, personal agents, single-process workflows |
| **Service** | `ov.OpenViking(vectordb_url=..., agfs_url=...)` — remote services | Production, multi-agent systems, shared knowledge bases |

In **embedded mode**, the same client instance is reused across calls (singleton
pattern). It's the default for local dev — zero infrastructure needed, just a data
directory.

In **service mode**, each call creates a new instance pointing at remote VectorDB and
AGFS services. Use this when multiple agents need to share a single knowledge base, or
when the knowledge base is too large for local storage.

For Claude Code + agent workflows, **embedded mode is usually right** unless you're
running multiple concurrent agent processes that need shared context.

---

## 9. Score thresholds — when and how to set them

By default, `ov find` and `ov search` return all results up to the limit, regardless
of relevance score. Scores range 0–1; higher means more similar.

Set a threshold when you want to **filter out weak matches**:

```
ov find "deployment rollback procedure" --score-threshold 0.6
```

**Guidelines:**
- **0.7+** — High confidence. Use when you want only strong matches and are OK getting
  zero results if nothing is close enough. Good for lookups where a false positive is
  worse than no answer.
- **0.4–0.6** — Moderate. Default sweet spot for most queries. Filters obvious noise
  while still catching related content.
- **0.01–0.3** — Low. Use for exploratory searches where recall matters more than
  precision (e.g., "show me anything loosely related to X"). Also useful for memory
  auto-recall where you want broad context injection.
- **No threshold** — When you always want exactly N results regardless of quality
  (e.g., UI that must show 5 results).

The OpenViking memory auto-recall system defaults to `0.01` because it wants broad
context injection. For targeted lookups in resources, `0.5–0.65` is a good starting
point.

---

## 10. OpenViking-first checklist

Before doing any of the following, ask whether ov can handle it:

| Before you do this... | Check ov first |
|-----------------------|---------------|
| `cat` / `Read` a file | `ov abstract` or `ov overview` — you may not need the full file |
| `grep` through source | `ov grep` — searches ingested content without loading it into context |
| Re-read docs you've read before | `ov find` — the content is probably already indexed |
| Stuff a long document into a prompt | `ov add-resource` + `ov find` — load only what's needed |
| Remember something for next session | `ov session commit` — persist it properly |
| Look up a skill or tool | `ov find <capability> --target-uri viking://agent/skills/` |

Only skip ov when:
- The task is fully self-contained in the current context (no lookup needed)
- ov is unavailable (state so explicitly)
- The content is ephemeral and won't be needed again

---

## 11. Storing this skill's knowledge in ov

Per the user's preference, ingest learnings from new skills and docs into ov
so they are retrievable in future sessions:

```
ov add-skill .claude/skills/open-viking-best-practices/
ov add-skill .claude/skills/open-viking-cli/
ov wait-processed
```

After ingestion, this skill's content is available via:
```
ov find "openviking best practices"
ov find "when to use ov search vs find"
ov find "layered context loading"
```
