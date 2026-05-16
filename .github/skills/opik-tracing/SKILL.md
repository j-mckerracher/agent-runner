---
name: opik-tracing
version: 1.0.0
description: >
  Opik v1 tracing and LLM observability guide for building observable AI applications.
  Use this skill when implementing distributed tracing, logging traces and spans, tracking multimodal data (images, audio, video),
  managing chat conversations with threads, annotating traces with feedback scores, cost tracking, and integrating with frameworks.
  Keywords: Opik, tracing, spans, @track decorator, log_traces, distributed tracing, multimodal traces,
  feedback scores, annotate traces, cost tracking, attachments, chat conversations, threads, observability,
  token usage, LLM logging, opik.Opik client.
---

# Opik Tracing & LLM Observability

## When to Use This Skill

Activate this skill when:

- Implementing observability for LLM applications using Opik SDKs (Python or TypeScript)
- Logging traces, spans, and multi-level execution hierarchies
- Tracking chat conversations and multi-turn interactions using threads
- Adding user feedback and scoring annotations to traces
- Monitoring token usage and cost across LLM calls
- Logging multimodal data (images, audio, video attachments)
- Setting up Opik projects, configuring API keys, and integrating with frameworks
- Exporting or importing trace data
- Debugging application flows using distributed tracing

---

## Key Concepts

**Trace** — Top-level record of a complete workflow or user interaction; contains input, output, metadata.

**Span** — Nested operation within a trace; represents discrete steps (e.g., retrieval, LLM call, post-processing).

**Thread** — Collection of related traces grouped by `thread_id` for multi-turn conversations and user sessions.

**@track Decorator** — Python (or TypeScript) function decorator that automatically logs function calls as traces/spans.

**Opik Client** — SDK instance (e.g., `opik.Opik()` or `new Opik()`) for programmatic logging.

**Feedback Scores** — Quantitative annotations (e.g., 0–1 scores) on traces or spans for quality evaluation.

**Attachments** — Multimodal data (images, video, audio, files) logged alongside traces.

**Cost Tracking** — Automatic estimation of LLM API costs by monitoring token usage and model metadata.

**Distributed Traces** — Parent–child trace relationships for complex workflows (e.g., agent tools calling other agents).

---

## Common Tasks

| Task | Canonical Doc |
|------|---------------|
| Log a trace or span with input/output | [Log traces](https://www.comet.com/docs/opik/v1/tracing/log_traces) |
| Track multi-turn chat and conversations | [Log chat conversations](https://www.comet.com/docs/opik/v1/tracing/log_chat_conversations) |
| Add user feedback and scoring annotations | [Annotate traces](https://www.comet.com/docs/opik/v1/tracing/annotate_traces) |
| Log images, audio, video, and file attachments | [Log media & attachments](https://www.comet.com/docs/opik/v1/tracing/log_multimodal_traces) |
| Monitor and retrieve token costs | [Cost tracking](https://www.comet.com/docs/opik/v1/tracing/cost_tracking) |
| Export traces and spans to CSV, JSON, or parquet | [Export data](https://www.comet.com/docs/opik/v1/tracing/export_data) |
| Bulk import/export via CLI | [Import/export commands](https://www.comet.com/docs/opik/v1/tracing/import_export_commands) |
| Use Opik Assist for LLM evaluations | [Opik Assist](https://www.comet.com/docs/opik/v1/tracing/opik_assist) |

---

## Observability Best Practices

1. **Start with clear trace boundaries.** Use one top-level trace per user interaction or business workflow, then break internal steps into spans so failures are easy to localize.
2. **Name spans for intent, not implementation detail.** Prefer names like `retrieve_context`, `tool_call.weather_api`, or `generate_answer` over generic labels such as `step_1`.
3. **Use `thread_id` consistently for multi-turn systems.** If an app has chat sessions, agent handoffs, or iterative workflows, always attach a stable thread identifier so you can reconstruct the full conversation.
4. **Add metadata that supports debugging.** Record project, model, provider, user/session identifiers, feature flags, and retrieval settings so you can filter traces by the factors that actually drive regressions.
5. **Capture feedback scores close to the trace.** Attach human or automated scores to traces and spans so observability feeds directly into evaluation and regression tracking.
6. **Protect sensitive data.** Avoid logging raw secrets, PII, or internal-only business content unless you have a sanitization strategy. Observability should improve debugging without expanding data risk.
7. **Instrument critical paths first.** Start with the main user flow, then deepen span coverage once the basic trace tree is stable. This matches Opik's guidance to grow observability incrementally.
8. **Use traces as the entry point for improvement.** When you find bad outputs, latency spikes, or costly paths, turn those traces into evaluation cases or follow-up experiments instead of treating tracing as a passive log sink.

**Best-practice references**
- Observability overview: https://www.comet.com/docs/opik/tracing/overview
- Tracing concepts + best practices: https://www.comet.com/docs/opik/tracing/concepts
- Production monitoring dashboards: https://www.comet.com/docs/opik/tracing/dashboards/production_monitoring

---

## Code Snippets

### Python: Log a trace with @track decorator

```python
import opik
from opik import opik_context

@opik.track
def my_llm_function(user_input):
    # Your LLM logic here
    response = "Generated response"
    
    # Optionally add feedback scores
    opik_context.update_current_trace(
        feedback_scores=[
            {"name": "user_feedback", "value": 0.9, "reason": "High quality"}
        ]
    )
    
    return response

# Log with thread_id for conversation grouping
my_llm_function("Hello", opik_args={"trace": {"thread_id": "user-123"}})
```

### TypeScript: Log a trace with Opik client

```typescript
import { Opik } from "opik";

const client = new Opik({
  apiKey: "your-api-key",
  projectName: "my-project",
});

const trace = client.trace({
  name: "chat_turn",
  input: { user_message: "What is Opik?" },
  output: { assistant: "Opik is an observability platform..." },
  threadId: "user-123", // Groups related traces
});

// Log feedback scores
client.logTracesFeedbackScores([
  { id: trace.data.id, name: "relevance", value: 0.95 }
]);

await client.flush();
```

### Python: Add multimodal attachments

```python
from opik import opik_context, track, Attachment

@track
def process_image(image_path):
    # Your image processing logic
    result = "Processed"
    
    opik_context.update_current_trace(
        attachments=[
            Attachment(
                data=image_path,
                content_type="image/png",
            )
        ]
    )
    
    return result
```

---

## Reference

- **Opik Python SDK**: `pip install opik` → [docs](https://www.comet.com/docs/opik/reference/python-sdk/overview)
- **Opik TypeScript SDK**: `npm install opik` → [docs](https://www.comet.com/docs/opik/reference/typescript-sdk/overview)
- **REST API**: [REST API Reference](https://www.comet.com/docs/opik/reference/rest-api/overview)
- **Environment Setup**: Configure `OPIK_API_KEY`, `OPIK_WORKSPACE`, and optionally `OPIK_URL_OVERRIDE`
- **Integrations**: LangChain, LlamaIndex, Anthropic, OpenAI, etc. → [Integration guide](https://www.comet.com/docs/opik/v1/integrations/overview)

**Quick Start**: [Opik Quickstart](https://www.comet.com/docs/opik/v1/quickstart)
