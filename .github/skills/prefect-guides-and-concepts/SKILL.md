---
name: prefect-guides-and-concepts
description: |
  Reference guide for Prefect v3 how-to guides and concepts. Use this skill when designing, implementing, deploying, operating, or debugging Prefect workflows, tasks, deployments, infrastructure, automations, configuration, cloud, self-hosted, migration, or AI integrations. Keywords: prefect, flow, task, deployment, work pool, worker, schedule, automation, event, artifact, asset, variable, block, settings, concurrency, cloud, server, migration, mcp
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is reference material for Prefect v3 docs structure and core mental models, so gather runtime-specific state only during a concrete Prefect task.

# Prefect v3 Guides and Concepts

Use this skill to ground Prefect work in the official v3 documentation set. Reach for `how-to-guides.md` when you need an implementation path and `concepts.md` when you need the underlying model or terminology.

## When to Use This Skill

Activate this skill when:

- Designing or implementing Prefect flows and tasks
- Choosing between direct calls, `.submit()`, and `.delay()`
- Configuring retries, logging, runtime access, caching, or concurrency
- Creating deployments, schedules, workers, or work pools
- Working with variables, blocks, secrets, settings, or profiles
- Building automations, triggers, notifications, or event-driven workflows
- Deciding between Cloud and self-hosted operating models
- Planning migrations or AI-assisted Prefect integrations

## Working Model

- **Flows** are the orchestration boundary: they accept parameters, track run state, and can be deployed remotely.
- **Tasks** are the retryable, cacheable, observable units of work inside or alongside flows.
- Use **direct task calls** for immediate sequential work, **`.submit()`** for concurrent work inside a flow, and **`.delay()`** for fire-and-forget/background task execution on separate infrastructure.
- **Deployments** package a flow entrypoint with remote execution settings such as schedules, work pools, and job variables.
- **Work pools** and **workers** determine where deployed flow runs execute.
- **Artifacts**, **assets**, logs, and runtime context improve observability and operator experience.
- **Variables**, **blocks**, and **settings/profiles** separate configuration from code.
- **Events**, **event triggers**, and **automations** let Prefect react to run state changes and external signals.

## Decision Rules

| Need | Start with | Reinforce with |
| --- | --- | --- |
| Write or refine workflow code | `how-to-guides.md` → Workflows | `concepts.md` → Flows, Tasks, States, Runtime context |
| Add retries, hooks, caching, or logging | `how-to-guides.md` → Workflows | `concepts.md` → States, Caching, Artifacts |
| Run work concurrently or in background | `how-to-guides.md` → Workflows | `concepts.md` → Tasks, Task runners, Global/tag-based concurrency |
| Deploy flows or schedule them | `how-to-guides.md` → Deployments | `concepts.md` → Deployments, Schedules, Work pools, Workers |
| Manage config and secrets | `how-to-guides.md` → Configuration | `concepts.md` → Variables, Blocks, Settings and profiles |
| Build reactive/event-driven automation | `how-to-guides.md` → Automations | `concepts.md` → Events, Automations, Event triggers |
| Choose execution infrastructure | `how-to-guides.md` → Workflow Infrastructure / Cloud / Self-hosted | `concepts.md` → Work pools, Server, Telemetry |
| Plan upgrades or ecosystem integrations | `how-to-guides.md` → AI / Migrate | `concepts.md` for the affected runtime model |

## Key Behavioral Notes

- Flow parameters are type-validated before a run starts; invalid deployment parameters fail before entering `Running`.
- Nested flows add first-class observability and can use their own task runners.
- A flow's final state is determined by raised exceptions, returned states, or the states behind returned futures.
- Task orchestration is client-side; Prefect resolves upstream futures automatically for downstream task inputs.
- Use `quote` or `opaque` annotations when future resolution or dataclass reconstruction would create unwanted side effects.
- Artifacts are operator-facing visual metadata; they complement, not replace, durable storage for large/private data.

## Reference Files in This Skill

- `how-to-guides.md` — full section-by-section coverage of the Prefect v3 how-to guides index.
- `concepts.md` — full section-by-section coverage of the Prefect v3 concepts index.
