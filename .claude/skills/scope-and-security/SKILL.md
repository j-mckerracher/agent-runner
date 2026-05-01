---
name: scope-and-security
description: |
  Scope boundaries, security enforcement, and forbidden action rules for AI agent workflows. Use this skill whenever an agent needs to understand what files it may access or modify, what actions are prohibited, or how to handle secrets and credentials. Covers: (1) Artifact root write permissions and path scoping, (2) Forbidden file patterns — *.env*, *secret*, *credential*, *password*, lock files, node_modules, dist, build, .git, (3) Forbidden actions — no HTTP requests to external URLs, no credential access, no installing global packages, (4) Secrets handling — never log secrets, reference by name only, (5) Scope creep prevention — stop and document when out-of-scope changes are needed. Keywords: scope boundaries, forbidden actions, forbidden files, security, secrets, credentials, environment variables, artifact root, write permissions, scope creep, file patterns, network restrictions.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is a policy document, so keep the base skill static and inject live state only for task-specific enforcement.

# Scope & Security Boundaries

Mandatory rules for file access, forbidden actions, secrets handling, and scope control in agent workflows.

## When to Use This Skill

Activate this skill when:

- An agent needs to determine what files it may read or modify
- An agent encounters files that might be sensitive (env files, credentials)
- An agent is about to make changes and needs to verify scope
- An agent needs to understand forbidden actions
- An agent discovers it needs to modify files outside its allowed scope

## Artifact Root Permissions

Agents may create and modify files within the artifact directory:

```
{{artifact_root}}{CHANGE-ID}/
```

This is **separate from the code repository** and is used for workflow artifacts, logs, and documentation.

### Code Repository Access

- **Read-only agents** (evaluators, QA, planners): May read but MUST NOT modify source code
- **Write agents** (software-engineer): May modify source code files within the designated `code_repo` as required by their Unit of Work
- **All agents**: May write to their designated artifact paths and `agent-context/lessons.md` (append-only)

## Forbidden File Patterns

Agents MUST NOT modify these files under any circumstances:

| Pattern                                  | Reason                                     |
| ---------------------------------------- | ------------------------------------------ |
| `*.env*`, `.env.*`                       | Environment secrets                        |
| `*secret*`, `*credential*`, `*password*` | Sensitive data                             |
| `package-lock.json`, `yarn.lock`         | Lock files (modify `package.json` instead) |
| `node_modules/`, `dist/`, `build/`       | Generated directories                      |
| `.git/`                                  | Version control internals                  |
| Agent prompt files                       | Configuration (unless explicitly tasked)   |

## Forbidden Actions

Agents MUST NOT perform these actions:

| Action                                               | Reason                       |
| ---------------------------------------------------- | ---------------------------- |
| Make HTTP/HTTPS requests to external URLs            | Network egress restriction   |
| Use `curl`, `wget`, `fetch` to external endpoints    | Network egress restriction   |
| Access credentials or environment variables directly | Security boundary            |
| Install global packages                              | System modification          |
| Modify system configuration                          | System modification          |
| Write files outside designated paths                 | Scope boundary               |
| Execute commands that transmit data externally       | Data exfiltration prevention |

## Secrets Handling Protocol

If an agent encounters what appears to be a secret (API key, password, token, credential):

1. **Do NOT include it in logs or artifacts**
2. **Do NOT echo it in command output**
3. **Reference it by name only** (e.g., "uses API_KEY environment variable")
4. **Escalate** if secret handling is required for the task
5. **Never commit** secrets into source code

## Scope Creep Prevention

If you discover you need to modify files outside your allowed scope:

1. **STOP** — do not make the change
2. **Document** the need in your output report
3. **Request** a scope expansion or task revision
4. **Escalate** if the task cannot be completed without the out-of-scope change

### Scope Creep Indicators

- Refactoring unrelated code
- Adding features not in the Definition of Done
- Changing formatting of untouched code
- Upgrading dependencies unless required
- Creating custom implementations when library features exist
- Modifying files not listed in implementation hints

## Lethal Trifecta Awareness

Prevent the dangerous overlap of:

1. **Private data access** — agents do NOT read .env files; secrets are not logged
2. **Untrusted content exposure** — user input validated at intake; agent output reviewed by evaluators
3. **Exfiltration capability** — agents have no network egress; artifacts stay in controlled paths

## Agent Network Restrictions

Agents MUST NOT:

- Make HTTP/HTTPS requests to external URLs
- Use network tools (`curl`, `wget`, `fetch`) to external endpoints
- Write files outside the designated artifact root or code repository
- Execute commands that transmit data externally

---

## Automated Scope Validation Script

Use `~/.github/scripts/validate-scope.py` to check file paths against forbidden patterns:

```bash
# Check specific files
~/.github/scripts/validate-scope.py [--artifact-root <path>] file1.ts file2.ts

# Check files from stdin
git diff --name-only | ~/.github/scripts/validate-scope.py
```

**What it checks**: All forbidden file patterns defined above (_.env_, _secret_, _credential_, _password_, lock files, node_modules/, dist/, build/, .git/). If `--artifact-root` is given, also validates paths are under that root.

**When to use**: Before committing changes, during evaluator gates, and as part of kill-switch enforcement.

**Output**: JSON to stdout with `status` (pass/fail), `files_checked`, and `violations` array.

**Exit codes**: 0 = all clean, 1 = violations found, 2 = usage error.
