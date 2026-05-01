# Prefect v3 how-to guides coverage

This file enumerates the sections and pages listed on the Prefect v3 **How-to guides** landing page.

## Workflows

- [`Write and run a workflow`](https://docs.prefect.io/v3/how-to-guides/workflows/write-and-run) — start here for basic flow/task authoring and execution.
- [`Use assets to track workflow outputs`](https://docs.prefect.io/v3/how-to-guides/workflows/assets) — publish and manage asset-oriented outputs.
- [`Automatically rerun a workflow when it fails`](https://docs.prefect.io/v3/how-to-guides/workflows/retries) — configure workflow retries.
- [`Manually retry a flow run`](https://docs.prefect.io/v3/how-to-guides/workflows/retry-flow-runs) — rerun failed runs operationally.
- [`Customize workflow metadata`](https://docs.prefect.io/v3/how-to-guides/workflows/custom-metadata) — set names, descriptions, and related metadata.
- [`Pass inputs to a workflow`](https://docs.prefect.io/v3/how-to-guides/workflows/pass-inputs) — define and supply parameters.
- [`Add logging`](https://docs.prefect.io/v3/how-to-guides/workflows/add-logging) — emit workflow and task logs.
- [`Access runtime information`](https://docs.prefect.io/v3/how-to-guides/workflows/access-runtime-info) — inspect run metadata from code.
- [`Run work concurrently`](https://docs.prefect.io/v3/how-to-guides/workflows/run-work-concurrently) — use task futures and dependencies.
- [`Cache workflow step outputs`](https://docs.prefect.io/v3/how-to-guides/workflows/cache-workflow-steps) — reuse results across runs.
- [`Run background tasks`](https://docs.prefect.io/v3/how-to-guides/workflows/run-background-tasks) — dispatch task work with `.delay()` and task workers.
- [`Respond to state changes`](https://docs.prefect.io/v3/how-to-guides/workflows/state-change-hooks) — attach hooks to runtime state transitions.
- [`Create Artifacts`](https://docs.prefect.io/v3/how-to-guides/workflows/artifacts) — publish links, Markdown, tables, progress, and images.
- [`Test workflows`](https://docs.prefect.io/v3/how-to-guides/workflows/test-workflows) — validate flow/task code safely.
- [`Apply global concurrency and rate limits`](https://docs.prefect.io/v3/how-to-guides/workflows/global-concurrency-limits) — protect shared resources across runs.
- [`Limit concurrent task runs with tags`](https://docs.prefect.io/v3/how-to-guides/workflows/tag-based-concurrency-limits) — constrain concurrency by task tag.

## Deployments

- [`Create Deployments`](https://docs.prefect.io/v3/how-to-guides/deployments/create-deployments) — package a flow for remote execution.
- [`Trigger ad-hoc deployment runs`](https://docs.prefect.io/v3/how-to-guides/deployments/run-deployments) — kick off deployment runs on demand.
- [`Create Deployment Schedules`](https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules) — run deployments on time-based schedules.
- [`Manage Deployment schedules`](https://docs.prefect.io/v3/how-to-guides/deployments/manage-schedules) — update, pause, or inspect schedules.
- [`Deploy via Python`](https://docs.prefect.io/v3/how-to-guides/deployments/deploy-via-python) — define deployment creation in Python.
- [`Define deployments with YAML`](https://docs.prefect.io/v3/how-to-guides/deployments/prefect-yaml) — manage deployment config declaratively.
- [`Retrieve code from storage`](https://docs.prefect.io/v3/how-to-guides/deployments/store-flow-code) — pull flow code from remote storage.
- [`Version Deployments`](https://docs.prefect.io/v3/how-to-guides/deployments/versioning) — track deployment versions intentionally.
- [`Override Job Variables`](https://docs.prefect.io/v3/how-to-guides/deployments/customize-job-variables) — customize infrastructure execution parameters.

## Configuration

- [`Store secrets`](https://docs.prefect.io/v3/how-to-guides/configuration/store-secrets) — manage sensitive values outside code.
- [`Share configuration between workflows`](https://docs.prefect.io/v3/how-to-guides/configuration/variables) — reuse variables across flows.
- [`Manage settings`](https://docs.prefect.io/v3/how-to-guides/configuration/manage-settings) — configure Prefect behavior with settings and profiles.

## Automations

- [`Create Automations`](https://docs.prefect.io/v3/how-to-guides/automations/creating-automations) — define event-driven reactions.
- [`Custom notifications`](https://docs.prefect.io/v3/how-to-guides/automations/custom-notifications) — send tailored alerts.
- [`Create Deployment Triggers`](https://docs.prefect.io/v3/how-to-guides/automations/creating-deployment-triggers) — launch flows from events.
- [`Chain Deployments with Events`](https://docs.prefect.io/v3/how-to-guides/automations/chaining-deployments-with-events) — build multi-step event-driven orchestration.
- [`Access parameters in templates`](https://docs.prefect.io/v3/how-to-guides/automations/access-parameters-in-templates) — reference runtime fields in templates.
- [`Pass event payloads to flows`](https://docs.prefect.io/v3/how-to-guides/automations/passing-event-payloads-to-flows) — hand event data into downstream flow parameters.

## Workflow Infrastructure

- [`Manage Work Pools`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/manage-work-pools) — create and administer execution pools.
- [`Run Flows in Local Processes`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/run-flows-in-local-processes) — use local process infrastructure.
- [`Run flows on serverless compute`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/serverless) — target serverless execution backends.
- [`Run flows in Docker containers`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/docker) — execute runs in Docker.
- [`Run flows in a static container`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/serve-flows-docker) — serve flows from a stable container image.
- [`Run flows on Kubernetes`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/kubernetes) — launch runs on Kubernetes.
- [`Run flows on Modal`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/modal) — use Modal as execution infrastructure.
- [`Run flows on Coiled`](https://docs.prefect.io/v3/how-to-guides/deployment_infra/coiled) — use Coiled-managed infrastructure.

## Prefect Cloud

- [`Connect to Prefect Cloud`](https://docs.prefect.io/v3/how-to-guides/cloud/connect-to-cloud) — configure access to Cloud.
- [`Manage Workspaces`](https://docs.prefect.io/v3/how-to-guides/cloud/workspaces) — operate multi-workspace setups.
- [`Create a Webhook`](https://docs.prefect.io/v3/how-to-guides/cloud/create-a-webhook) — accept inbound events through webhooks.
- [`Troubleshoot Prefect Cloud`](https://docs.prefect.io/v3/how-to-guides/cloud/troubleshoot-cloud) — diagnose Cloud-specific issues.

## Prefect Self-hosted

- [`Run a local Prefect server`](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-cli) — start a local server from the CLI.
- [`Run the Prefect server in Docker`](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-docker) — containerize the server.
- [`Run Prefect on Windows`](https://docs.prefect.io/v3/how-to-guides/self-hosted/server-windows) — self-host in Windows environments.
- [`Run the Prefect Server via Docker Compose`](https://docs.prefect.io/v3/how-to-guides/self-hosted/docker-compose) — bring up server components with Compose.

## AI

- [`Overview`](https://docs.prefect.io/v3/how-to-guides/ai) — orient on Prefect AI-related guidance.
- [`Use the Prefect MCP server`](https://docs.prefect.io/v3/how-to-guides/ai/use-prefect-mcp-server) — integrate assistants with Prefect through MCP.

## Migrate

- [`Migrate from Airflow`](https://docs.prefect.io/v3/how-to-guides/migrate/airflow) — map Airflow concepts and workflows to Prefect.
- [`Upgrade to Prefect 3.0`](https://docs.prefect.io/v3/how-to-guides/migrate/upgrade-to-prefect-3) — handle the major-version upgrade path.
- [`Upgrade from agents to workers`](https://docs.prefect.io/v3/how-to-guides/migrate/upgrade-agents-to-workers) — adopt the newer worker model.
- [`Transfer resources between environments`](https://docs.prefect.io/v3/how-to-guides/migrate/transfer-resources) — move resources across workspaces or environments.
