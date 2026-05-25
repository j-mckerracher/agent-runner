---
name: opik-reference
version: 1.0.0
description: >
  Opik reference documentation for all SDKs, REST API, and query language.
  Use this skill for Opik SDK reference, Python SDK, TypeScript SDK, REST API, OQL (Opik Query Language),
  opik.Opik client, opik-ts, @track decorator, evaluate function, and API method lookups.
  Keywords: Opik, SDK reference, Python SDK, TypeScript SDK, REST API, OQL, Opik Query Language,
  opik.Opik, opik-ts, @track, evaluate.
---

## When to Use This Skill

Activate this skill when:

- Looking up Python SDK methods and classes (`opik.Opik`, `@opik.track`, etc.)
- Querying TypeScript SDK APIs (`opik-ts`, `Opik` client, `@track` decorator)
- Building OQL filter expressions for `searchPrompts()`, `searchTraces()`, `searchThreads()`
- Working with REST API endpoints (traces, spans, datasets, experiments, etc.)
- Understanding evaluation workflows (`evaluate` function, metrics, experiments)
- Debugging REST client methods and response structures
- Navigating Opik's core resource types and lifecycle operations

---

## Reference Map

### Python SDK
**Base URL:** https://www.comet.com/docs/opik/python-sdk-reference/

Key sub-areas:
- **Client initialization** — `opik.Opik()` constructor and configuration
- **Tracing** — `@opik.track` decorator, span management, context capture
- **REST client** — `client.rest_client` access to all platform functionality
- **Experiments** — Running evaluations and tracking results
- **Datasets** — Creating, managing, and versioning evaluation data

### TypeScript SDK
**Base URL:** https://www.comet.com/docs/opik/reference/typescript-sdk/

Key sub-areas:
- **Setup** — `opik-ts` CLI tool for project configuration and installation
- **Client** — `Opik` class initialization and project binding
- **Tracing** — `@track` decorator, nested spans, async context handling
- **Search APIs** — `searchPrompts()`, `searchTraces()`, `searchThreads()` with OQL filters
- **Evaluation** — `evaluate()` function, custom metrics, experiment tracking

### REST API
**Base URL:** https://www.comet.com/docs/opik/reference/rest-api/

Key sub-areas:
- **Traces** — Create, retrieve, search, update, delete trace records
- **Spans** — Manage nested spans within traces
- **Datasets** — CRUD operations on evaluation datasets and items
- **Experiments** — Create experiments, add results, track feedback scores
- **Projects** — Workspace and project management
- **Prompts** — Manage prompt templates and versions
- **Feedback Definitions** — Define custom feedback score types
- **Automation Rules** — Set up evaluators and automated feedback
- **System** — Health checks, workspace config, usage metrics

### OQL (Opik Query Language)
**Base URL:** https://www.comet.com/docs/opik/reference/typescript-sdk/opik-query-language

Key operators:
- **String** — `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with`
- **Comparison** — `>`, `<`, `>=`, `<=`
- **Null checks** — `is_empty`, `is_not_empty`
- **DateTime** — ISO 8601 format (`"2024-01-01T00:00:00Z"`)
- **List operations** — `tags contains "value"`
- **Logical** — `AND` (only; `OR` not supported)

---

## Key Symbols

### Python SDK

| Symbol | Purpose |
|--------|---------|
| `opik.Opik()` | Main client; access traces, datasets, experiments, rest_client |
| `@opik.track` | Decorator to auto-trace function calls; captures inputs, outputs, duration |
| `opik.Span` | Manual span creation for detailed observability |
| `client.rest_client.traces` | REST client for trace CRUD and search operations |
| `client.rest_client.datasets` | Manage datasets and dataset items |
| `client.rest_client.experiments` | Run and track evaluation experiments |

### TypeScript SDK

| Symbol | Purpose |
|--------|---------|
| `Opik` class | Main client; initialize with API key and project name |
| `@track` decorator | Auto-trace async/sync functions; captures all inputs/outputs |
| `client.searchPrompts()` | Search prompts with OQL filter string |
| `client.searchTraces()` | Search traces with OQL and project scope |
| `client.searchThreads()` | Search threads by status, duration, feedback scores |
| `evaluate()` function | Run evaluations over datasets with custom metrics |

---

## Code Snippets

### Python: Basic Initialization and Tracing

```python
import opik

# Initialize Opik client (uses OPIK_API_KEY and OPIK_URL env vars)
client = opik.Opik()

# Use @track decorator to auto-trace a function
@opik.track
def my_llm_call(question: str) -> str:
    response = "Sample answer"
    return response

# Access REST client for advanced operations
traces = client.rest_client.traces.search_traces(
    project_name="my-project",
    filters=[{"field": "name", "operator": "contains", "value": "important"}],
    max_results=100
)
```

### TypeScript: Search with OQL and Evaluation

```typescript
import { Opik } from "opik";

const client = new Opik({
  apiKey: process.env.OPIK_API_KEY,
  projectName: "default",
});

// Search traces with OQL filter
const goodTraces = await client.searchTraces({
  projectName: "my-project",
  filterString: 'feedback_scores.accuracy > 0.9 AND tags contains "production"'
});

// Evaluate a dataset with custom metric
const results = await evaluate({
  dataset: "my-dataset",
  task: async (input) => {
    const response = await myModel.generate(input.prompt);
    return { output: response };
  },
  metrics: [customAccuracyMetric, customLatencyMetric],
});
```

---

## Reference

**Overview & Getting Started:**
- https://www.comet.com/docs/opik/reference/overview

**Python SDK:**
- https://www.comet.com/docs/opik/python-sdk-reference/
- REST client: https://www.comet.com/docs/opik/reference/python-sdk/rest-api

**TypeScript SDK:**
- https://www.comet.com/docs/opik/reference/typescript-sdk/overview
- Opik TS CLI: https://www.comet.com/docs/opik/reference/typescript-sdk/opik-ts
- OQL: https://www.comet.com/docs/opik/reference/typescript-sdk/opik-query-language
- Evaluation: https://www.comet.com/docs/opik/reference/typescript-sdk/evaluation/overview

**REST API:**
- https://www.comet.com/docs/opik/reference/rest-api/overview

**Cookbook (How-To Guides):**
- https://www.comet.com/docs/opik/integrations/overview
