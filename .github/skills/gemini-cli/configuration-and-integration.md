# Gemini CLI configuration and integration

This file covers the parts of Gemini CLI that shape long-term behavior: context files, settings, custom commands, skills, extensions, hooks, MCP servers, and tool behavior.

## `GEMINI.md` and hierarchical context

Gemini loads context from `GEMINI.md` files hierarchically:

1. `~/.gemini/GEMINI.md`
2. workspace and parent-directory `GEMINI.md` files
3. just-in-time `GEMINI.md` files near files the agent touches

Use `GEMINI.md` for durable project rules such as coding standards, testing expectations, preferred architecture, and negative constraints.

High-value interactive commands:

- `/memory show`
- `/memory reload`
- `/memory add <text>`

## Settings locations

- User settings: `~/.gemini/settings.json`
- Workspace settings: `<project>/.gemini/settings.json`

Important settings areas include:

- `general.defaultApprovalMode`
- `general.plan.enabled`
- `general.plan.directory`
- `model.name`
- `output.format`
- `tools.shell.enableInteractiveShell`
- `tools.useRipgrep`
- `security.*`
- `context.*`

Use `/settings` for interactive editing when possible.

## Security, sandboxing, and trust

- Use `--sandbox` for stronger isolation when running commands in unfamiliar projects.
- Use `/permissions trust` to manage trusted folders.
- Approval mode and policy settings determine how aggressively Gemini can execute tools.
- Sensitive environment variables are treated specially in several integration paths, especially MCP and hooks.

When giving operational advice, prefer the least-permissive mode that still gets the job done.

## Plan mode

Plan mode is Gemini's read-only planning surface.

- CLI flag: `gemini --approval-mode=plan`
- Interactive entry: `/plan`
- Quick toggle: `Shift+Tab`

Use it when the task needs research, design, or agreement before changes.

## Custom commands

Custom commands live in TOML files:

- Global: `~/.gemini/commands/`
- Project: `<project>/.gemini/commands/`

Rules:

- Project commands override global commands with the same name.
- Subdirectories create namespaced commands such as `/git:commit`.
- `{{args}}` injects command arguments.
- `!{...}` injects shell-command output.
- `@{...}` injects file or directory content.

Use custom commands for repeatable prompts, internal workflows, changelog helpers, commit-message generation, and review templates.

## Skills

Official Gemini CLI skill docs describe workspace skills under `.gemini/skills/` or `.agents/skills/`, with user-level skills under `~/.gemini/skills/`.

Common management commands:

```bash
gemini skills list
gemini skills install <source>
gemini skills link <path>
gemini skills enable <name>
gemini skills disable <name>
gemini skills uninstall <name>
```

Interactive skill management includes `/skills list`, `/skills reload`, `/skills enable`, `/skills disable`, and `/skills link`.

Important nuance: the local terminal help may expose fewer actions than the broader docs or the interactive slash-command surface.

## Extensions

Extensions can bundle prompts, MCP servers, custom commands, themes, hooks, subagents, and skills.

Common terminal commands:

```bash
gemini extensions list
gemini extensions install <source>
gemini extensions link <path>
gemini extensions validate <path>
gemini extensions update --all
```

Use extensions when you want to package and share a larger capability set than a single skill.

## Hooks

Hooks let you intercept Gemini's lifecycle with synchronous scripts.

Typical uses:

- inject context before model execution
- validate or block tool calls
- log activity
- redact or transform outputs

Important hook facts:

- hooks communicate structured JSON over `stdout`
- debug logging should go to `stderr`
- hooks are configured in `settings.json`
- project hooks are security-sensitive and fingerprinted

Docs describe rich interactive `/hooks` management. In the current local terminal help, `gemini hooks` exposes `migrate`, so verify the installed surface before giving shell commands.

## MCP servers

MCP servers extend Gemini with external tools and resources.

Two practical control planes exist:

1. **Terminal command group**

```bash
gemini mcp add <name> <commandOrUrl> [args...]
gemini mcp list
gemini mcp enable <name>
gemini mcp disable <name>
gemini mcp remove <name>
```

2. **`settings.json` configuration**

Configure `mcpServers` entries with fields such as:

- `command` or `url` / `httpUrl`
- `args`
- `env`
- `cwd`
- `timeout`
- `trust`
- `includeTools`
- `excludeTools`

Use `@server://resource/path` to reference MCP resources in prompts when a server exposes them.

## Built-in tool model

Gemini's core tools include file reads/edits, shell execution, glob/grep search, web search/fetch, `ask_user`, memory, plan-mode transitions, and skill activation.

Working guidance:

- Prefer read/search tools before editing.
- Use `@path` to inject files directly into prompts.
- Use `!command` for direct shell execution when you want manual control.
- Keep an eye on approvals, policies, and sandboxing when tools can mutate state.

## Local-help-first checklist

For any concrete Gemini CLI task:

1. Check `gemini --help`
2. Check `gemini <group> --help`
3. Use the docs to clarify behavior, configuration shape, or workflow details
4. If docs and local help differ, tell the user exactly which surface you are following
