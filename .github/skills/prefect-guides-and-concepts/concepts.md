# Prefect v3 concepts coverage

This file enumerates the sections and pages listed on the Prefect v3 **Concepts** landing page.

## Workflows

- [`Flows`](https://docs.prefect.io/v3/concepts/flows) — orchestration boundary, parameters, subflows, final-state behavior, and deployment entrypoints.
- [`Tasks`](https://docs.prefect.io/v3/concepts/tasks) — retryable/cacheable work units, direct calls, `.submit()`, and `.delay()`.
- [`Assets`](https://docs.prefect.io/v3/concepts/assets) — track workflow outputs as managed assets.
- [`Caching`](https://docs.prefect.io/v3/concepts/caching) — understand result reuse and cache behavior.
- [`States`](https://docs.prefect.io/v3/concepts/states) — learn how Prefect models run lifecycle and outcomes.
- [`Runtime context`](https://docs.prefect.io/v3/concepts/runtime-context) — inspect flow/task metadata from execution context.
- [`Artifacts`](https://docs.prefect.io/v3/concepts/artifacts) — operator-facing visual metadata in standardized formats.
- [`Task runners`](https://docs.prefect.io/v3/concepts/task-runners) — choose execution strategy for submitted tasks.
- [`Global concurrency limits`](https://docs.prefect.io/v3/concepts/global-concurrency-limits) — coordinate shared-resource protection globally.
- [`Tag-based concurrency limits`](https://docs.prefect.io/v3/concepts/tag-based-concurrency-limits) — gate concurrency by task tag.

## Deployments

- [`Deployments`](https://docs.prefect.io/v3/concepts/deployments) — the packaged remote execution definition for a flow.
- [`Schedules`](https://docs.prefect.io/v3/concepts/schedules) — time-based triggers for deployment runs.
- [`Work pools`](https://docs.prefect.io/v3/concepts/work-pools) — abstract execution backends and queueing targets.
- [`Workers`](https://docs.prefect.io/v3/concepts/workers) — the processes that poll work pools and launch runs.

## Configuration

- [`Variables`](https://docs.prefect.io/v3/concepts/variables) — shared non-secret configuration.
- [`Blocks`](https://docs.prefect.io/v3/concepts/blocks) — structured configuration and external service credentials.
- [`Settings and profiles`](https://docs.prefect.io/v3/concepts/settings-and-profiles) — environment-level Prefect configuration.
- [`Prefect server`](https://docs.prefect.io/v3/concepts/server) — self-hosted API/UI architecture and behavior.
- [`Telemetry`](https://docs.prefect.io/v3/concepts/telemetry) — understand emitted diagnostics and product telemetry.

## Automations

- [`Events`](https://docs.prefect.io/v3/concepts/events) — the normalized event model.
- [`Automations`](https://docs.prefect.io/v3/concepts/automations) — rules that react to events.
- [`Event triggers`](https://docs.prefect.io/v3/concepts/event-triggers) — matching logic that decides when automations fire.

## Prefect Cloud

- [`Rate limits and data retention`](https://docs.prefect.io/v3/concepts/rate-limits) — Cloud service limits and retention boundaries.
- [`SLAs`](https://docs.prefect.io/v3/concepts/slas) — service-level constructs and expectations.
- [`Webhooks`](https://docs.prefect.io/v3/concepts/webhooks) — inbound integrations for event-driven workflows.
