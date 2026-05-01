---
name: playwright-cli
version: 1.0.0
description: |
  Use the Playwright CLI to drive a browser from the terminal for web app testing, debugging, screenshots, tracing, storage state, and session management. Activate when a task involves browser automation with `playwright-cli`, snapshot element refs, or quick Playwright-based verification.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. The CLI is already stateful through browser sessions, so add only task-specific context such as the target URL, session name, or output path.

# Playwright CLI

Use `playwright-cli` for terminal-first browser automation when you want fast, token-efficient web interaction without loading a large MCP schema or page tree into context.

## When to Use

Activate this skill when:

- Reproducing or verifying a browser flow from the terminal
- Testing a local dev server or deployed web app without writing a full Playwright spec first
- Capturing snapshots, screenshots, PDFs, traces, or videos during debugging
- Inspecting console messages, network activity, cookies, or browser storage
- Preserving browser state across commands with named sessions or storage state files
- Generating Playwright locators from a live page element

## Core workflow

1. Choose a session strategy before acting. Use the default session for one-off work, or `-s=<name>` for isolated multi-step tasks.
2. Start or attach to a browser with `open` or `attach`.
3. Get element refs with `snapshot`, then prefer those refs for interactions.
4. Drive the page with focused commands like `click`, `fill`, `press`, `select`, `hover`, and navigation commands.
5. Inspect state with `console`, `network`, `eval`, `cookie-*`, `localstorage-*`, or `sessionstorage-*` as needed.
6. Save artifacts with `screenshot`, `pdf`, `tracing-*`, `video-*`, or `state-save` when the task calls for evidence or reuse.
7. Close or detach cleanly when done.

## Session and startup patterns

```bash
# Open a browser and navigate immediately
playwright-cli open https://example.com

# Show the browser window when visual inspection matters
playwright-cli open https://example.com --headed

# Use an isolated named session for longer workflows
playwright-cli -s=todo-app open https://demo.playwright.dev/todomvc/

# Reuse browser state across restarts
playwright-cli -s=todo-app open https://example.com --persistent

# Attach to an external browser instead of launching a new one
playwright-cli attach --cdp=http://localhost:9222
playwright-cli attach --extension=chrome
```

## Snapshot-first interaction loop

Prefer refs from snapshots over brittle selectors whenever possible.

```bash
playwright-cli snapshot
playwright-cli click e15
playwright-cli fill e21 "user@example.com"
playwright-cli press Enter

# Limit snapshot size when you only need shallow structure
playwright-cli snapshot --depth=4

# Snapshot a specific element
playwright-cli snapshot e21
```

Refs are usually the best default. When necessary, `playwright-cli` also accepts CSS selectors and Playwright locator expressions.

```bash
playwright-cli click "#main button[type=submit]"
playwright-cli click "getByRole('button', { name: 'Submit' })"
playwright-cli click "getByTestId('save-button')"
```

## High-value commands

### Navigation and actions

```bash
playwright-cli goto https://example.com/settings
playwright-cli go-back
playwright-cli go-forward
playwright-cli reload
playwright-cli hover e9
playwright-cli check e12
playwright-cli uncheck e12
playwright-cli select e18 "dark"
playwright-cli drag e4 e11
playwright-cli upload ./fixtures/avatar.png
playwright-cli dialog-accept
playwright-cli dialog-dismiss
```

### Inspection and debugging

```bash
playwright-cli console
playwright-cli console warning
playwright-cli network
playwright-cli eval "document.title"
playwright-cli eval "el => el.textContent" e15
playwright-cli generate-locator e15
playwright-cli highlight e15
playwright-cli highlight --hide
playwright-cli show
```

### Artifacts

```bash
playwright-cli screenshot
playwright-cli screenshot e15 --filename=button.png
playwright-cli pdf --filename=page.pdf
playwright-cli tracing-start
playwright-cli tracing-stop
playwright-cli video-start debug.webm
playwright-cli video-chapter "Before submit"
playwright-cli video-stop
```

### State and storage

```bash
playwright-cli state-save auth.json
playwright-cli state-load auth.json

playwright-cli cookie-list
playwright-cli cookie-get session_id
playwright-cli localstorage-list
playwright-cli localstorage-get theme
playwright-cli sessionstorage-list
```

### Network control

```bash
playwright-cli route "**/api/**" --status=200 --body='{"ok":true}'
playwright-cli route-list
playwright-cli unroute "**/api/**"
```

## Output modes

- Use `--json` when you want structured command output.
- Use `--raw` when you want only the command result value for piping into other tools.
- Use `snapshot --filename=<file>` or `screenshot --filename=<file>` when the task needs durable artifacts instead of ephemeral output.

```bash
playwright-cli list --json
playwright-cli --raw eval "JSON.stringify(location.href)"
playwright-cli snapshot --filename=after-login.yaml
```

## Session management

```bash
playwright-cli list
playwright-cli tab-list
playwright-cli tab-new https://example.com/help
playwright-cli tab-select 1
playwright-cli tab-close 1
playwright-cli close
playwright-cli detach
playwright-cli close-all
playwright-cli kill-all
playwright-cli delete-data
```

## Rules

- Use the `playwright-cli` command name, not `playwright`.
- Prefer named sessions with `-s=<name>` when multiple browser workflows may run concurrently.
- Prefer snapshot refs for interaction targets before falling back to selectors or raw coordinates.
- Keep the browser headless by default; add `--headed` only when human observation will help.
- Use `--raw` or `--json` for automation instead of scraping human-readable output.
- Save and load storage state only when the task needs auth reuse or reproducibility.
- Use `detach` only when connected to an external browser that should remain open; otherwise use `close`.

## Validation

Confirm the CLI is available and the skill guidance matches the installed command surface:

```bash
playwright-cli --help
playwright-cli open https://example.com
playwright-cli snapshot
playwright-cli close
```
