---
name: opik-production-monitoring
version: 1.0.0
description: >
  Opik v1 production monitoring, online evaluation rules, LLM gateway integration, guardrails, PII anonymizers, alerts, and dashboards.
  Use this skill when monitoring LLM applications, defining scoring rules, protecting sensitive data, configuring alerts, or visualizing production metrics.
  Keywords: Opik, production monitoring, online evaluation rules, guardrails, PII anonymizer, LLM gateway, dashboards, webhooks, alerts, trace monitoring, feedback scores.
---

## When to Use

Activate this skill when:

- Defining automated scoring rules using LLM as a Judge metrics
- Setting up guardrails to detect risks like PII leaks or off-topic responses
- Configuring anonymizers to protect sensitive information in traces
- Integrating with LLM gateways (Opik, Kong, Helicone, LiteLLM, etc.)
- Creating webhook alerts (Slack, PagerDuty, custom endpoints)
- Building dashboards for cross-project metrics, cost, latency, feedback scores
- Logging and monitoring feedback scores for production traces
- Auditing and exporting production trace data

---

## Key Concepts

**Online Evaluation Rules**: Automatically score production traces using LLM as a Judge metrics. Opik provides built-in rules (Hallucination, Moderation, Answer Relevance) and supports custom prompts with variable mapping. Rules run at configurable sampling rates (e.g., 100% of traces).

**Guardrails**: Detect and prevent risks in LLM inputs/outputs. Includes PII detection (via NLP tokenization), Topic classification (zero-shot small model), and Custom guardrails (user-defined logic). Returns pass/fail status for each trace.

**Anonymizers**: Automatically replace PII and sensitive data before storage. Supports regex rules, function-based rules, and hash-based masking for privacy compliance.

**LLM Gateway**: Centralize access to multiple LLM providers (OpenAI, Anthropic, etc.) via a single proxy endpoint. Opik integrates with Kong, Helicone, LiteLLM, Portkey, and others.

**Alerts & Webhooks**: Trigger HTTP POST notifications to Slack, PagerDuty, or custom endpoints on events: trace errors, feedback scores, prompt changes, cost/latency thresholds.

**Dashboards**: Monitor production metrics via Insights (in-project views) or Workspace Dashboards (cross-project). Track trace volume, cost, latency, errors, feedback scores, and compare experiment results.

---

## Common Tasks

| Task | Doc URL |
|------|---------|
| Define a scoring rule in the UI or via REST API | https://www.comet.com/docs/opik/v1/production/rules |
| Configure PII, Topic, or Custom guardrails | https://www.comet.com/docs/opik/v1/production/guardrails |
| Set up regex or function-based anonymizers | https://www.comet.com/docs/opik/v1/production/anonymizers |
| Integrate with gateway provider (Kong, Helicone, LiteLLM) | https://www.comet.com/docs/opik/v1/production/gateway |
| Create Slack or webhook alerts | https://www.comet.com/docs/opik/v1/production/alerts |
| Build custom dashboard or Insights view | https://www.comet.com/docs/opik/v1/production/dashboards |
| Log feedback scores manually or update via search API | https://www.comet.com/docs/opik/v1/production/production_monitoring |

---

## Code Snippets

### Define a Scoring Rule via REST API

```python
import requests

rule_payload = {
    "name": "hallucination_detector",
    "sampling_rate": 1.0,  # 100% of traces
    "model": "gpt-4",
    "prompt": "Check if the LLM response contains hallucinated facts. {{context}} {{llm_output}}",
    "variable_mapping": {
        "context": "input",
        "llm_output": "output"
    },
    "score_definition": {
        "name": "hallucination",
        "type": "boolean"
    }
}

response = requests.post(
    "https://opik.comet.ml/api/v1/rules",
    json=rule_payload,
    headers={"Authorization": f"Bearer {OPIK_API_KEY}"}
)
```

### Custom Guardrail Example

```python
import opik
from opik.opik_context import get_current_trace_data

opik_client = opik.Opik()

def custom_competitor_guardrail(generation: str, trace_id: str) -> str:
    competitors = ["OpenAI", "Anthropic", "Google AI"]
    guardrail_span = opik_client.span(
        name="CompetitorBlock",
        input={"generation": generation},
        type="guardrail",
        trace_id=trace_id
    )
    
    found = [c for c in competitors if c.lower() in generation.lower()]
    result = "failed" if found else "passed"
    
    guardrail_span.end(output={
        "guardrail_result": result,
        "blocked_competitors": found
    })
    
    return generation
```

### Anonymizer with Regex Rules

```python
from opik.anonymizer import create_anonymizer
import opik

email_rule = {
    "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "replace": "[EMAIL]"
}

phone_rule = (r"\b\d{3}-\d{3}-\d{4}\b", "[PHONE]")

anonymizer = create_anonymizer([email_rule, phone_rule])
opik.hooks.add_anonymizer(anonymizer)
```

---

## Reference

- **Rules**: Define LLM as a Judge metrics with sampling, variable mapping, score definitions. Opik supports built-in (Hallucination, Moderation, Answer Relevance) and custom prompts.
- **Guardrails** (self-hosted): PII detection, Topic classification, Custom logic. Returns pass/fail per trace and spans.
- **Anonymizers**: Regex, function, hash-based rules applied transparently to all ingested data.
- **Gateway**: Proxy integration with Kong, Helicone, LiteLLM, Portkey, Vercel AI Gateway, OpenRouter, AISuite, Anannas, Opik LLM Gateway.
- **Alerts**: Webhook triggers (Slack, PagerDuty, General) with thresholds, windows, custom headers, secret tokens.
- **Dashboards**: Multi-project (time series, metrics), Experiments (leaderboard), Insights (in-project custom views).
- **Search & Update**: Use `Opik.search_traces()` and `Opik.log_traces_feedback_scores()` for batch feedback operations.

For detailed configuration and code samples, see the production docs.
