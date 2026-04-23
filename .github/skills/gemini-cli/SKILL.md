---
name: gemini-cli
description: Comprehensive guide for the Google Gemini CLI. Use this skill when users ask how to install, authenticate, configure, automate, extend, or troubleshoot the `gemini` command, slash commands, `GEMINI.md`, plan mode, headless mode, skills, hooks, extensions, MCP servers, sessions, or approval modes.
---

## Dynamic context

No default `!` pre-execution injection is recommended. For concrete Gemini CLI work, inspect the installed binary first with `gemini --version`, `gemini --help`, and the relevant `gemini <subcommand> --help`.

# Gemini CLI

Use this skill to help with the Google Gemini CLI from a GitHub Copilot session.

## Grounding rules

- **Start with the local install.** Treat `gemini --help` and subcommand help as the source of truth for the currently installed command surface.
- **Use the official docs for depth.** Use `https://geminicli.com/docs/` for workflows, settings, architecture, and feature details.
- **Do not flatten interactive and shell surfaces together.** Distinguish slash commands used *inside* Gemini from `gemini ...` commands run in the terminal.
- **Explain mismatches plainly.** If the docs describe a broader feature set than the local binary exposes, prefer the local behavior for concrete instructions and mention the difference.

## Working model

- `gemini` starts the interactive REPL.
- `gemini -p "..."` runs headless/non-interactive output.
- `gemini "..."` seeds an interactive session with an initial prompt.
- `gemini -i "..."` runs a prompt and stays interactive.
- `gemini -r latest` resumes the latest session.
- Approval modes are `default`, `auto_edit`, `plan`, and `yolo`.
- Output formats are `text`, `json`, and `stream-json`.

## Decision rules

| Need | Start with | Reinforce with |
| --- | --- | --- |
| Install or sign in | `usage-reference.md` → install/auth sections | local `gemini --help`, official installation/auth docs |
| Daily CLI usage | `usage-reference.md` → invocation, sessions, shell/web workflows | command/subcommand `--help` |
| Headless scripting or JSON output | `usage-reference.md` → automation/headless | headless docs plus local flags |
| Context, memory, and plan mode | `usage-reference.md` → sessions/workflows | `configuration-and-integration.md` → `GEMINI.md`, settings |
| Skills, extensions, custom commands, hooks, or MCP | `configuration-and-integration.md` | local subcommand help and official docs |
| Troubleshooting feature availability | local `gemini <subcommand> --help` | explain any docs-vs-install gap |

## High-value behavioral notes

- The local CLI help is the best way to confirm which command groups are installed right now.
- The docs describe richer **interactive slash-command** workflows than the terminal `gemini <group>` commands alone.
- `GEMINI.md` is hierarchical project context, not a one-off prompt scratchpad.
- Plan mode is intentionally read-only and is meant for research and agreement before edits.
- Headless mode is the right fit for automation, CI, and scripts; interactive mode is the right fit for exploratory work.

## Current local command surface observed from `gemini --help`

- Top level: `mcp`, `extensions`, `skills`, `hooks`
- `gemini skills`: `list`, `enable`, `disable`, `install`, `link`, `uninstall`
- `gemini mcp`: `add`, `remove`, `list`, `enable`, `disable`
- `gemini extensions`: `install`, `uninstall`, `list`, `update`, `disable`, `enable`, `link`, `new`, `validate`, `config`
- `gemini hooks`: currently exposes `migrate`

Use the docs to explain the broader Gemini ecosystem, but use the local help output when telling someone exactly what to run on this machine.

## Reference files in this skill

- `usage-reference.md` — install, auth, invocation modes, daily commands, sessions, shell/web workflows, headless mode, and shortcuts
- `configuration-and-integration.md` — `GEMINI.md`, settings, custom commands, skills, extensions, hooks, MCP servers, and tool behavior
