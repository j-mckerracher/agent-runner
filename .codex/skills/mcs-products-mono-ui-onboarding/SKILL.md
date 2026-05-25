---
name: mcs-products-mono-ui-onboarding
description: >
  Quick-start guide for understanding the MCS Products Mono UI workspace structure,
  Nx workflows, and testing commands.
---

# MCS Products Mono UI Onboarding

Use this skill as the first stop when you need to orient yourself in this repository.
It is optimized for fast answers about workspace layout, Nx targets, and testing.

## When to use

Use this skill when you need to:

- explain how the repository is organized
- find the main apps, libraries, and shared areas
- choose the right Nx command for serve, build, lint, unit test, component test, or e2e
- understand the repo's testing strategy and harness conventions
- identify the most authoritative files before doing deeper exploration

## Fastest source-of-truth files

Start with these files before exploring widely:

| File                                              | Why it matters                                                                              |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `README.md`                                       | High-level repo overview, key apps, and common Nx command shapes                            |
| `AGENTS.md`                                       | Best repo-specific summary of architecture, testing strategy, and gotchas                   |
| `docs/Nx-Workspace.md`                            | Quick Nx usage guide for day-to-day commands                                                |
| `docs/Testing.md`                                 | Testing strategy: Cypress component tests first, Jest for pure logic, E2E for flows         |
| `docs/Testing/Test_Harnesses.md`                  | Canonical harness and `data-test-id` guidance                                               |
| `docs/Testing/Cypress-Login.md`                   | Shared `cy.login()` flow, env-based credentials, and MFA/TOTP setup                         |
| `nx.json`                                         | Workspace-wide target defaults, plugin wiring, caching, and Chrome defaults                 |
| `package.json`                                    | Wrapper scripts for env-specific Cypress runs, Docker flows, and report aggregation         |
| `tsconfig.base.json`                              | Workspace alias families such as `@rls/*`, `@mcs/*`, `@mpc/*`, plus `/testing` entry points |
| `apps/**/project.json` and `libs/**/project.json` | The exact targets and options for a specific project                                        |

If you need project-specific targets quickly, prefer checking that project's `project.json`.
If the environment supports it, `npx nx show project <project-name>` is also a fast way to inspect a project's root and targets.

## Workspace map

This is an Nx monorepo for Mayo Collaborative Services frontend applications.

### Main application areas

- `apps/pearls/`
  - PEARLS / RLS applications such as:
    - `rls-accession-portal-ui`
    - `rls-specimen-accessioning`
    - `rls-sendouts-ui`
    - `rls-order-system-viewer-app`
    - `rls-core-home-app`
- `apps/product-catalog/`
  - Product Catalog applications such as `prodcat-nexus-app`
- `apps/web-server/`
  - Local server and hosting support

### Main library areas

- `libs/pearls/`
- `libs/product-catalog/`
- `libs/shared/`
- `libs/storybook-host/`

### Library organization pattern

Most feature areas follow this split:

- `common/` = models, constants, types, testing locators
- `data-access/` = services, API access, state
- `ui/` = presentational components
- `features/` = smart/container components

Prefer workspace aliases over deep relative imports.
Common alias families include:

- `@rls/*`
- `@mcs/*`
- `@mpc/*`

Many libraries also expose `/testing` entry points.
Prefer those exported testing APIs over deep test-only imports when available.

## Preferred Nx command patterns

Use direct Nx commands as the default workflow.
If `nx` is not on the shell `PATH`, use `npx nx` with the same arguments.

### Core commands

```bash
nx serve <project>
nx build <project>
nx lint <project>
nx test <project>
nx component-test <project> --browser chrome
nx e2e <e2e-project> --browser chrome
nx open-cypress <project> --component --browser chrome
nx open-cypress <e2e-project> --e2e --browser chrome
```

### Multi-project commands

```bash
nx affected:test
nx affected:component-test
nx affected:e2e
nx affected:lint
nx affected:build

nx run-many -t build
nx run-many -t lint
```

### Useful examples in this repo

```bash
nx serve rls-specimen-accessioning
nx component-test rls-sendouts-ui --browser chrome
nx component-test product-details-ui --browser chrome
nx e2e rls-specimen-accessioning-e2e --browser chrome
nx open-cypress rls-specimen-accessioning-e2e --e2e --browser chrome
```

### Choosing between Nx and package.json scripts

Default to direct Nx commands.
Use `package.json` scripts mainly when you specifically need:

- environment-specific Cypress base URLs
- Dockerized Cypress flows
- report merging or coverage aggregation

`package.json` contains many useful wrappers, but some are inconsistent.
Do not assume a wrapper script is the safest default when a direct Nx command exists.

## Which test target should you run?

Use this decision guide:

- **Angular component behavior or UI interactions**
  - Run `nx component-test <project> --browser chrome`
- **Pure functions, utilities, and business logic**
  - Run `nx test <project>`
- **Cross-page flows, deployed integration, or full app behavior**
  - Run `nx e2e <e2e-project> --browser chrome`
- **Interactive debugging in Cypress**
  - Run `nx open-cypress <project> --component --browser chrome`
  - Or `nx open-cypress <e2e-project> --e2e --browser chrome`

## Testing conventions to remember

- Cypress component tests are the primary testing strategy in this repo.
- Jest is mainly for pure logic and utility code, not most Angular component behavior.
- E2E is used for app-level workflows, smoke coverage, and integration flows.
- Prefer writing or updating tests before code changes when feasible.
- Chrome is the default browser for Cypress work in this workspace.

### Harness and selector rules

- Use test harnesses/page objects instead of scattering raw selectors in tests.
- Use `data-test-id` attributes for interactive elements.
- Reuse helpers from `@rls/common-testing` where possible.
- Check `docs/Testing/Test_Harnesses.md` before inventing a new harness pattern.

## E2E authentication guidance

For Cypress E2E work:

- prefer the shared `cy.login()` command
- provide credentials through environment variables or `cypress.env.json`
- never commit usernames, passwords, or TOTP secrets
- use `docs/Testing/Cypress-Login.md` as the source of truth for AAD and MFA behavior

## Repo-specific gotchas

- `component-test`, `e2e`, `open-cypress`, and `run-cypress` default to Chrome in `nx.json`
- If Nx returns stale results, add `--skip-nx-cache`
- If the workspace behaves strangely, `npx nx reset` can help clear Nx state
- Some builds and local workflows depend on `apps/web-server/`
- The repo has many reporting scripts in `package.json`; use them only when the task specifically needs merged reports
- PrimeNG behavior may be affected by repo-specific patching in the prepare hooks; check existing scripts before changing library or build plumbing

## Angular and repo conventions worth assuming

- standalone Angular components
- signals-first state management
- `ChangeDetectionStrategy.OnPush`
- modern control flow (`@if`, `@for`, `@switch`)
- avoid `ngClass` and `ngStyle` when direct bindings are sufficient

## Quick response pattern

When a user asks a repo-orientation question, answer from this guide first.
If deeper detail is needed, then narrow exploration in this order:

1. `README.md`
2. `AGENTS.md`
3. `docs/Nx-Workspace.md`
4. `docs/Testing.md`
5. `docs/Testing/Test_Harnesses.md` or `docs/Testing/Cypress-Login.md`
6. `nx.json`, `package.json`, `tsconfig.base.json`
7. the specific `project.json` for the app or lib in question

This keeps repo-orientation work fast, accurate, and consistent.
