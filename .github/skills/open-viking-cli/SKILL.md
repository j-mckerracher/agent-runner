---
name: ov
description: Use the OpenViking Rust CLI (ov) to operate OpenViking over HTTP—ingest resources/skills, browse the VikingFS, run retrieval, manage sessions/relations, and inspect system state. This skill lists ov commands and operations only.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is command reference material, so default shell injection would add noise more often than value.

# ov — OpenViking CLI Skill (Operations Only)

**Scope**  
This skill documents the **`ov`** CLI command surface and behavior. `ov` talks to a running OpenViking HTTP server (it does **not** run in embedded mode). Connection details (URL, API key, agent id, default output) are read from `~/.openviking/ovcli.conf` unless overridden by CLI arguments or env vars. [1](https://deepwiki.com/volcengine/OpenViking/10.2-cli-commands-reference)[2](https://github.com/volcengine/OpenViking/blob/main/docs/en/api/01-overview.md)[3](https://deepwiki.com/volcengine/OpenViking/8.2-cli-commands-reference)

---

## Global CLI Behavior

- **Invocation**: `ov [GLOBAL OPTIONS] <COMMAND> [ARGS]` (all commands contact the HTTP server). [1](https://deepwiki.com/volcengine/OpenViking/10.2-cli-commands-reference)
- **Global options**:
  - `-o, --output {table|json}` — select output format (human vs. machine). [1](https://deepwiki.com/volcengine/OpenViking/10.2-cli-commands-reference)
  - `-c, --compact` — compact representation (e.g., single‑line JSON). [1](https://deepwiki.com/volcengine/OpenViking/10.2-cli-commands-reference)
- **Config resolution order**: CLI args → environment variables (e.g., `OPENVIKING_URL`, `OPENVIKING_API_KEY`) → `ovcli.conf`. [3](https://deepwiki.com/volcengine/OpenViking/8.2-cli-commands-reference)

---

## Command Groups & Operations

Below are the **operations the `ov` CLI supports**, grouped by functional area.

### 1) Resource & Skill Management

- **`add-resource <path|url>`** — Ingest local files/directories or URLs into VikingFS; supports waiting for async processing. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`add-skill <path|url>`** — Ingest skills (e.g., code/tools) so agents can call them. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`export <uri> --out <file.ovpack>`** — Export a subtree as an `.ovpack` archive. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`import <file.ovpack> [--target <uri>]`** — Import an `.ovpack` archive into VikingFS. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

> **Path note**: `add-resource` and `add-skill` accept both URLs and filesystem paths; the CLI resolves local relative paths to absolute before sending, avoiding server‑CWD ambiguity (fix landed recently). [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)[5](https://github.com/volcengine/OpenViking/issues/230)

### 2) Filesystem (VikingFS) Operations

- **`ls <uri> [--recursive]`** — List directory contents. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`tree <uri>`** — Display a directory tree under a URI. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`mkdir <uri>`** — Create a directory. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`rm <uri>`** — Remove a resource or directory. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`mv <src> <dst>`** — Move/rename a resource. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`stat <uri>`** — Show metadata for a resource. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 3) Content Access (L0/L1/L2)

- **`abstract <uri>`** — Fetch L0 abstract. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`overview <uri>`** — Fetch L1 overview. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`read <uri>`** — Fetch L2 full content. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 4) Retrieval & Search

- **`find "<query>" [--limit N] [--threshold F]`** — Semantic retrieval (top‑K style). [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`search "<query>" [--under <uri>]`** — Context‑aware search scoped to a subtree. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`grep "<pattern>" [--under <uri>]`** — Content pattern search (regex/text). [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`glob "<pattern>"`** — Filesystem glob matching (e.g., `**/*.md`). [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 5) Sessions & Memory

- **`session new [--title <t>]`** — Create a session. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`session list`** — List sessions. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`session get --id <id>`** — Get session details. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`session delete --id <id>`** — Delete a session. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`session add-message --id <id> --role <role> --content "<text>"`** — Append a message. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`session commit --id <id>`** — Commit the session and extract memories. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 6) Relations (Knowledge Graph)

- **`relations <uri>`** — List relations for a node. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`link --from <uri> --to <uri> --type <rel>`** — Create a relation link. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`unlink --from <uri> --to <uri> --type <rel>`** — Remove a relation link. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 7) System & Observability

- **`system wait`** — Block until current background processing completes. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`system status`** — Show component status. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`system health`** — Health check summary. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`observer queue`** — Inspect ingest/processing queues. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`observer vikingdb`** — Inspect vector index backend. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`observer vlm`** — Inspect VLM status. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

### 8) Config Utilities

- **`config show`** — Echo effective CLI configuration (as resolved). [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)
- **`config validate`** — Validate CLI configuration. [4](https://github.com/billyndroid/openviking/tree/main/crates/ov_cli)

---

## Output & Scripting Notes

- Prefer `-o json` for automation; `--compact` yields single‑line JSON for shell pipelines. [1](https://deepwiki.com/volcengine/OpenViking/10.2-cli-commands-reference)
- Environment variables like `OPENVIKING_URL` and `OPENVIKING_API_KEY` can override connection settings if you don’t want to edit `ovcli.conf`. [3](https://deepwiki.com/volcengine/OpenViking/8.2-cli-commands-reference)

---

## Minimal Examples (CLI‑only)

```bash
# Ingest a URL and wait for processing
ov add-resource https://example.com/docs --wait                 # list & tree afterwards
ov ls viking://resources --recursive                            # browse
ov tree viking://resources                                      # view structure

# Retrieval
ov find "API authentication flow" --limit 5 --threshold 0.7     # semantic top-K
ov grep "Bearer " --under viking://resources                    # pattern search

# Access L0/L1/L2
ov abstract viking://resources/project/readme.md                # L0
ov overview viking://resources/project/readme.md                # L1
ov read     viking://resources/project/readme.md                # L2

# Sessions
SID=$(ov session new --title "triage" -o json | jq -r .id)
ov session add-message --id "$SID" --role user --content "Summarize auth setup"
ov session commit --id "$SID"

# Relations
ov link --from viking://resources/guide.md \
        --to   viking://skills/deploy.sh \
        --type references
``
```
