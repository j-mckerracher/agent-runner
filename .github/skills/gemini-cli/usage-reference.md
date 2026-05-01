# Gemini CLI usage reference

This file covers the parts of Gemini CLI most people need first: installation, authentication, invocation modes, daily workflows, sessions, shell/web usage, and automation.

## Install and runtime basics

- Recommended runtime: **Node.js 20+**
- Common install:

```bash
npm install -g @google/gemini-cli
```

- Run without a permanent install:

```bash
npx @google/gemini-cli
```

- Stable is the default channel; preview and nightly exist for newer features.

## Authentication decision table

| Situation | Start with | Key setup |
| --- | --- | --- |
| Local interactive usage with a personal Google account | `gemini` then **Sign in with Google** | Browser-based sign-in, cached locally |
| API-key-based usage | `GEMINI_API_KEY` | `export GEMINI_API_KEY="..."` |
| Vertex AI with ADC | `gcloud auth application-default login` | also set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` |
| Vertex AI with service account | `GOOGLE_APPLICATION_CREDENTIALS` | also set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` |
| Headless or CI usage | API key or Vertex AI env vars | do not depend on an interactive browser flow |

## Core invocation patterns

| Command | Meaning |
| --- | --- |
| `gemini` | Start an interactive session |
| `gemini "Explain this repo"` | Start interactively with an initial prompt |
| `gemini -i "Run checks and keep going"` | Execute a prompt, then remain interactive |
| `gemini -p "Summarize README.md"` | Headless/non-interactive run |
| `cat log.txt \| gemini -p "Summarize failures"` | Use piped input in headless mode |
| `gemini -r latest` | Resume the latest session |
| `gemini -r latest "Continue"` | Resume and immediately continue with a new prompt |

## High-value global flags

| Flag | Meaning |
| --- | --- |
| `-m, --model` | Select a model or alias |
| `-p, --prompt` | Headless prompt |
| `-i, --prompt-interactive` | Prompt, then stay interactive |
| `-r, --resume` | Resume a prior session |
| `-o, --output-format` | `text`, `json`, or `stream-json` |
| `--approval-mode` | `default`, `auto_edit`, `plan`, or `yolo` |
| `-s, --sandbox` | Run in a sandboxed environment |
| `--include-directories` | Add more directories to the workspace |
| `--list-sessions` / `--delete-session` | Manage saved sessions from the shell |

## Model selection

Common aliases from the official CLI reference:

| Alias | Typical use |
| --- | --- |
| `auto` | default routing |
| `pro` | heavier reasoning |
| `flash` | fast, balanced work |
| `flash-lite` | simplest and cheapest tasks |

Use `-m <model-or-alias>` or interactive model controls when you need to force behavior.

## Approval modes and safety

- **`default`**: confirm tool use as needed
- **`auto_edit`**: auto-approve edit tools
- **`plan`**: read-only planning mode
- **`yolo`**: auto-approve everything; use carefully

For risky or unfamiliar projects, prefer `--sandbox` and avoid `yolo`.

## Interactive commands worth remembering

| Command | Use |
| --- | --- |
| `/help` | show interactive help |
| `/resume` | browse and resume prior sessions |
| `/plan` | enter plan mode |
| `/settings` | open the settings editor |
| `/memory show` | inspect loaded `GEMINI.md` context |
| `/memory reload` | reload context files |
| `/permissions trust` | manage trusted folders |
| `/extensions ...` | manage extensions interactively |
| `/skills ...` | manage skills interactively |
| `/mcp ...` | inspect MCP servers, tools, and resources |
| `/hooks ...` | inspect or toggle hooks |
| `/shells` | inspect background shells |
| `/tools` | inspect active tools |

## Sessions and rewind

- Resume the most recent work with `gemini -r latest` or `/resume`.
- List or delete saved sessions from the shell with:

```bash
gemini --list-sessions
gemini --delete-session 1
```

- Use `/rewind` or press **Esc** twice to browse rewind points.
- Rewind can undo chat history, file changes, or both.

## Running shell commands

- Prefix a command with `!` inside Gemini to run it directly, for example `!git status`.
- Typing `!` on an empty prompt can enter shell mode for repeated manual commands.
- Agent-initiated shell commands normally require approval.
- Use Gemini for loops like “run tests, inspect the failure, fix it, rerun”.

## Web search and fetch

- Use Gemini search for up-to-date information.
- Use direct fetch for specific URLs when you already know the source.
- A reliable pattern is: **search → fetch → implement**.

## Headless mode and automation

Headless mode is the scripting surface.

```bash
gemini -p "Summarize the open TODOs"
gemini -p "Return JSON only" --output-format json
gemini -p "Stream progress" --output-format stream-json
```

### Output behavior

- `text`: plain terminal output
- `json`: one final JSON object with `response`, `stats`, and optional `error`
- `stream-json`: newline-delimited events such as `init`, `message`, `tool_use`, `tool_result`, `error`, and `result`

### Exit codes

- `0`: success
- `1`: general error/API failure
- `42`: invalid input/arguments
- `53`: turn limit exceeded

## High-value keyboard shortcuts

| Shortcut | Meaning |
| --- | --- |
| `Shift+Tab` | cycle approval modes |
| `Ctrl+L` | clear and redraw the UI |
| `Ctrl+R` | reverse-search prompt history |
| `Esc` twice | open rewind/history flow |
| `Ctrl+G` | open the current prompt in an external editor |
| `Ctrl+B` | toggle background shell visibility |
| `F12` | open debug/error details |

## Practical guidance

- Use interactive mode for exploratory work and multi-step collaboration.
- Use headless mode for automation, CI, and structured output.
- Use local `gemini <subcommand> --help` before assuming a documented flag is present in the installed version.
