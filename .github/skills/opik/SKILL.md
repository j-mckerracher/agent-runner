# Opik — LLM Observability & Optimization

Opik is an open-source platform (by Comet) for logging, debugging, and optimizing AI agents and LLM applications. Source: https://www.comet.com/docs/opik/

---

## Table of Contents

1. [What Opik Is](#what-opik-is)
2. [Installation & Configuration](#installation--configuration)
3. [Core Concepts](#core-concepts)
4. [Tracing / Observability](#tracing--observability)
5. [Evaluation](#evaluation)
6. [Metrics Reference](#metrics-reference)
7. [Agent Optimization](#agent-optimization)
8. [Production Features](#production-features)
9. [Self-Hosting](#self-hosting)
10. [Rate Limits & Limits](#rate-limits--limits)
11. [Best Practices](#best-practices)
12. [Troubleshooting](#troubleshooting)

---

## What Opik Is

Opik provides end-to-end tooling for the AI engineering lifecycle:

- **Observability** — Full trace/span capture for every LLM call, tool invocation, and retrieval step
- **Evaluation** — Test suites (pass/fail assertions) and dataset-driven quantitative scoring
- **Optimization** — Six automated prompt optimization algorithms
- **Production monitoring** — Online evaluation, gateway guardrails, alerts
- **Prompt management** — Versioned system prompts, Prompt Playground, A/B comparison

Deployment options: Opik Cloud (free/paid), open-source self-hosted (Docker or Kubernetes), enterprise.

---

## Installation & Configuration

### Python

```bash
pip install opik
opik configure          # interactive: sets API key + workspace
```

### TypeScript

```bash
npm install opik
```

### Environment Variables

```bash
OPIK_API_KEY=<your-api-key>          # Cloud only
OPIK_WORKSPACE=<workspace-name>      # optional (Cloud)
OPIK_PROJECT_NAME=<project-name>     # optional, defaults to "Default Project"
OPIK_URL_OVERRIDE=<instance-url>     # self-hosted only
OPIK_BASE_URL=http://localhost:5173/api   # local Docker instance
OPIK_TRACK_DISABLE=true              # globally disable tracing
```

### AI-Assisted Setup (fastest)

```bash
npx skills add comet-ml/opik-skills
# Then in your coding agent: "Instrument my agent with Opik using /instrument"
```

Works with Claude Code, Cursor, Codex, OpenCode.

### Diagnostics

```bash
opik healthcheck     # verify config + backend connectivity
opik configure       # reconfigure credentials
```

---

## Core Concepts

| Concept | Description |
|---------|-------------|
| **Trace** | Complete execution path for one user interaction; has unique ID, timing, inputs/outputs, metadata |
| **Span** | Individual operation within a trace (LLM call, tool use, retrieval); hierarchical parent-child |
| **Thread** | Group of related traces forming a multi-turn conversation; use consistent `thread_id` |
| **Metric** | Quantitative score on a trace/span output (heuristic or LLM-as-judge) |
| **Dataset** | Collection of test cases with inputs and optional expected outputs |
| **Experiment** | Record of dataset evaluation: every item, agent output, and metric scores |
| **Test Suite** | LLM-judged pass/fail assertions for behavioral regression testing |
| **Optimization Run** | Automated prompt refinement using one of six algorithms |

**Span types:** `general`, `tool`, `llm`, `guardrail`

**Datetime format:** ISO 8601 UTC — `YYYY-MM-DDTHH:MM:SS.ffffffZ`

---

## Tracing / Observability

### Python — `@track` Decorator (recommended)

```python
import opik
import openai

client = openai.OpenAI()

@opik.track
def retrieve_context(input_text):
    return ["retrieved fact 1", "retrieved fact 2"]

@opik.track
def generate_response(input_text, context):
    full_prompt = f"Context: {', '.join(context)}\nUser: {input_text}\nAI:"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": full_prompt}]
    )
    return response.choices[0].message.content

@opik.track(name="my_llm_application", project_name="my-project")
def llm_chain(input_text):
    context = retrieve_context(input_text)
    return generate_response(input_text, context)
```

Nested `@track` functions automatically become child spans of the outermost trace.

### Adding Metadata, Tags, Thread IDs at Runtime

```python
import opik

@opik.track
def llm_chain(text: str) -> str:
    # Update the current trace
    opik.opik_context.update_current_trace(
        tags=["llm_chatbot"],
        metadata={"version": "1.0"},
        thread_id="conversation-123",           # groups traces into a Thread
        feedback_scores=[{"name": "user_rating", "value": 5}]
    )
    # Update the current span
    opik.opik_context.update_current_span(
        metadata={"model": "gpt-4o"},
        usage={"prompt_tokens": 100, "completion_tokens": 50}
    )
    return f"Processed: {text}"
```

### Passing `opik_args` per Call

```python
result = llm_chain(
    "hello world",
    opik_args={
        "span": {"tags": ["llm"], "metadata": {"version": "1.0"}},
        "trace": {
            "thread_id": "conversation-123",
            "tags": ["user-session"],
            "metadata": {"user_id": "user-456"}
        }
    }
)
```

### Python Context Managers

```python
import opik

with opik.start_as_current_trace("my-trace", project_name="my-project") as trace:
    trace.input = {"user_query": "What is the weather?"}
    trace.output = {"response": "Sunny today!"}
    trace.tags = ["weather"]
    trace.metadata = {"model": "gpt-4o", "temperature": 0.7}

    with opik.start_as_current_span("llm-call", type="llm") as span:
        span.input = {"prompt": "Explain quantum computing"}
        span.output = {"response": "Quantum computing uses qubits..."}
        span.model = "gpt-4o"
        span.provider = "openai"
        span.usage = {"prompt_tokens": 10, "completion_tokens": 50}
```

### Low-Level Python SDK

```python
from opik import Opik

client = Opik(project_name="my-project")

trace = client.trace(
    name="my_trace",
    input={"user_question": "Hello?"},
    output={"response": "Hi!"}
)

trace.span(
    name="retrieval",
    input={"query": "Hello?"},
    output={"docs": ["doc1", "doc2"]}
)

trace.span(
    name="llm_call",
    type="llm",
    input={"prompt": "..."},
    output={"response": "..."}
)

trace.end()
client.flush()   # required for short-lived scripts
```

### Low-Level TypeScript SDK

```typescript
import { Opik } from "opik";

const client = new Opik({
    apiUrl: "https://www.comet.com/opik/api",
    apiKey: "your-api-key",
    projectName: "your-project-name",
});

const trace = client.trace({
    name: "Trace",
    input: { prompt: "Hello!" },
    output: { response: "Hello, world!" },
});

const span = trace.span({
    name: "llm-call",
    type: "llm",
    input: { prompt: "Hello, world!" },
    output: { response: "Hello, world!" },
});

await client.flush();
```

### TypeScript Decorators (Experimental, v5+)

```typescript
import { track } from "opik";

class TranslationService {
    @track({ type: "llm" })
    async generateText() { return "Generated text"; }

    @track({ name: "translate" })
    async translate(text: string) { return `Translated: ${text}`; }

    @track({ name: "process", projectName: "translation-service" })
    async process() {
        const text = await this.generateText();
        return this.translate(text);
    }
}
```

### Project Context

```python
# Option 1: decorator param
@opik.track(project_name="my_project")
def my_function(): pass

# Option 2: client
from opik import Opik
client = Opik(project_name="my_project")

# Option 3: context manager (overrides for a code block)
with opik.project_context("customer-support"):
    my_agent(query)
```

Project name resolution order: explicit argument → client config → `OPIK_PROJECT_NAME` env var → "Default Project"

### Flush & Tracing Control

```python
client.flush()                     # ensures delivery before process exits

@opik.track(flush=True)            # flush after each decorated call
def llm_chain(input_text): ...

opik.set_tracing_active(False)     # disable dynamically
opik.set_tracing_active(True)      # re-enable
opik.is_tracing_active()           # check status
```

### OpenAI Integration

```python
from opik.integrations.openai import track_openai
from openai import OpenAI

openai_client = track_openai(OpenAI())
# All calls via openai_client are now traced automatically
```

### Framework Integrations (40+)

| Framework | Integration |
|-----------|-------------|
| OpenAI (Python/TS) | `track_openai()` / `trackOpenAI()` |
| LangChain | `OpikTracer` callback |
| LangGraph | `OpikTracer` callback |
| LlamaIndex | native integration |
| CrewAI | native integration |
| Anthropic | native integration |
| AWS Bedrock | native integration |
| Google Gemini | native integration |
| Groq, Mistral, Cohere, DeepSeek | native integrations |
| LiteLLM / OpenRouter | gateway integrations |
| Ragas | evaluation integration |
| Dify / Flowise | no-code integrations |
| ADK | `track_adk_agent_recursive()` |
| AI Vercel SDK | `OpikExporter` + NodeSDK |
| OpenTelemetry | OTLP endpoint + headers |

---

## Evaluation

### Two Approaches

| | Test Suites | Datasets & Metrics |
|--|-------------|-------------------|
| **Output** | Pass / Fail | Numeric scores |
| **Judge** | LLM judge (natural language) | Heuristic or LLM metrics |
| **Best for** | Behavioral regression, prompt iteration | Quality benchmarking, RAG evaluation |
| **Grows from** | Production failures | Curated test sets |

### The Evaluation Loop (Recommended Workflow)

1. **Find issue** — Browse traces in dashboard, filter by error/low score
2. **Add to test suite** — Write assertion capturing expected behavior
3. **Fix** — Update prompt, tools, or retrieval config
4. **Validate** — Run suite; confirm fix + no regressions
5. **Repeat** — Suite grows organically from real failures

### Test Suites — Python

```python
import opik
from opik.integrations.openai import track_openai
from openai import OpenAI

openai_client = track_openai(OpenAI())
opik_client = opik.Opik()

suite = opik_client.get_or_create_test_suite(
    name="customer-support-qa",
    project_name="my-agent",
    global_assertions=[
        "The response directly addresses the user's question",
        "The response is concise (3 sentences or fewer)",
        "The response is grounded in the provided context",
    ],
    global_execution_policy={"runs_per_item": 2, "pass_threshold": 2},
)

suite.insert([
    {"data": {"question": "How do I create a project?", "context": "Click New Project in the Dashboard."}},
    {"data": {"question": "What are pricing tiers?", "context": "Free ($0), Pro ($29/month), Enterprise (custom)."}},
])

def task(item):
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Answer based ONLY on the provided context."},
            {"role": "user", "content": f"Question: {item['question']}\n\nContext: {item['context']}"},
        ],
    )
    return {"input": item, "output": response.choices[0].message.content}

result = opik.run_tests(test_suite=suite, task=task)
print(f"Pass rate: {result.pass_rate:.0%}")
```

**Execution policy:** `runs_per_item: 3, pass_threshold: 2` = item passes if ≥2 of 3 runs pass (handles LLM non-determinism). Item-level policies override suite-level.

### Datasets & Metrics — Python

```python
import opik
from opik.evaluation import evaluate
from opik.evaluation.metrics import Hallucination, AnswerRelevance

opik.configure()
client = opik.Opik()

dataset = client.get_or_create_dataset(name="my-eval-dataset")
dataset.insert([
    {"input": "What is the capital of France?", "expected_output": "Paris"},
    {"input": "What is 2+2?", "expected_output": "4"},
])

def task(item):
    result = call_llm(item["input"])
    return {"output": result}

evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[Hallucination(), AnswerRelevance()],
    experiment_name="my-experiment-v1",
)
```

### Annotation / Human Feedback

- **SDK:** `log_traces_feedback_scores()` or `opik_context.update_current_trace(feedback_scores=[...])`
- **UI:** Open a trace → Annotate sidebar → multiple reviewers supported, average shown
- **Online Evaluation:** Auto-scores production traces via LLM-as-judge rules with configurable sampling rates

---

## Metrics Reference

### Heuristic Metrics (deterministic)

| Metric | What it checks |
|--------|---------------|
| `Equals` | Exact string match |
| `Contains` | Substring presence |
| `RegexMatch` | Regex pattern match |
| `IsJson` | Valid JSON output |
| `ROUGE` | Overlap with reference text |
| `BERTScore` | Semantic similarity |
| `Levenshtein` | Edit distance |
| `BLEU` / `Sentence BLEU` / `Corpus BLEU` | N-gram precision |
| `ChrF` | Character n-gram F-score |
| `GLEU` | Google BLEU |
| `Readability` | Flesch-Kincaid readability |
| `Sentiment` | Positive/negative/neutral |
| `Tone` | Tone classification |
| `Language Adherence` | Language consistency |
| `JSDivergence` / `JSDistance` / `KLDivergence` | Distribution distance |
| `Spearman Ranking` | Rank correlation |

**Conversation heuristic:**
- `DegenerationC` — Detects repetition/degeneration across conversation
- `Knowledge Retention` — Checks if last reply preserves user facts from earlier turns

### LLM-as-Judge Metrics (semantic)

| Metric | What it evaluates |
|--------|------------------|
| `Hallucination` | Factual claims not grounded in context |
| `AnswerRelevance` / `QA Relevance Judge` | Answer matches the question |
| `ContextPrecision` | Retrieved context precision |
| `ContextRecall` | Retrieved context recall |
| `Moderation` | Safety / harmful content |
| `G-Eval` | General quality (configurable rubric) |
| `MeaningMatch` | Semantic match to expected output |
| `Summarization Coherence/Consistency` | Summary quality |
| `Dialogue Helpfulness Judge` | Helpfulness in conversation |
| `Agent Task Completion Judge` | Did agent complete the task? |
| `Agent Tool Correctness Judge` | Were correct tools used? |
| `Trajectory Accuracy` | Multi-step agent path correctness |
| `Compliance Risk Judge` | Regulatory/policy compliance |
| `Structured Output Compliance` | JSON/schema adherence |
| `Prompt Uncertainty Judge` | Confidence in answer |
| `LLM Juries Judge` | Multi-LLM consensus scoring |
| `Usefulness` | General usefulness to user |

**Conversation LLM judges:**
- `Conversational Coherence`
- `Session Completeness Quality`
- `User Frustration`

### Custom Model for Metrics

```python
from opik.evaluation.metrics import Hallucination

# Use any LiteLLM-supported model
metric = Hallucination(model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0")
```

---

## Agent Optimization

Opik Agent Optimizer automatically refines prompts using your datasets and metrics.

### Six Algorithms

| Algorithm | Best For |
|-----------|----------|
| **MetaPrompt** | General clarity/wording improvements; iterative LLM critique |
| **HRPO** (Hierarchical Reflective Prompt Optimizer) | Complex prompts; analyzes failure batches systematically |
| **Few-shot Bayesian Optimization** | Finding optimal few-shot example combinations (Optuna) |
| **Evolutionary Optimization** | Novel prompt structures; multi-objective; genetic algorithms |
| **GEPA** | Single system prompt optimization for single-turn tasks |
| **Parameter Optimization** | Tuning temperature, top_p via Bayesian search |

### Workflow

1. Prepare datasets and metrics in Opik
2. Select optimizer algorithm
3. Review results in dashboard
4. Deploy via Deploy button (agents pick up new config without code changes)

### No-Code Option

Use **Optimization Studio** in the UI for no-code optimization runs.

---

## Production Features

### Online Evaluation

Automatically scores traces as they come in using LLM-as-judge rules. Configure sampling rate and which metrics to apply per project.

### Gateway & Guardrails

Route LLM calls through Opik's proxy to enforce safety rules, content policies, and cost controls before responses reach users.

### Alerts

Configure threshold-based alerts on metric scores, error rates, latency, or cost to get notified when production quality degrades.

### Agent Configuration (Prompt Management)

- Store versioned system prompts and model parameters in Opik
- Agents fetch active configuration at runtime → update prompts without code deploys
- Side-by-side comparison + one-click rollback

### Prompt Playground

- Test prompt variations across multiple models
- Evaluate against datasets inline
- Use as LLM proxy for cost-free experimentation

---

## Self-Hosting

### Local (Docker) — for development only

```bash
git clone https://github.com/comet-ml/opik.git
cd opik
./opik.sh          # Linux/Mac — starts at http://localhost:5173
.\opik.ps1         # Windows
```

Point SDK at local instance:
```bash
export OPIK_BASE_URL=http://localhost:5173/api
```

### Kubernetes — production

Follow the Kubernetes deployment guide: https://www.comet.com/docs/opik/self-host/overview

Keep server and SDKs on latest versions for compatibility.

---

## Rate Limits & Limits

| Limit | Value |
|-------|-------|
| Requests/minute per user (global) | 2,000 (burst: +100) |
| Events/minute ingestion per user | 10,000 |
| Events/minute per workspace per user | 5,000 |
| Get span by ID (retrieval endpoint) | 250 req/min |

---

## Best Practices

### Tracing

- **Align trace boundaries with user interactions** — one trace = one user request
- **Use descriptive span names** — makes debugging faster
- **Always set `thread_id`** for multi-turn conversations to group traces into Threads
- **Add `user_id`, `session_id` in metadata** for filtering and analysis
- **Avoid logging PII** in trace inputs/outputs
- **Call `client.flush()`** at end of short-lived scripts or lambdas
- **Use `project_name`** to organize different applications or environments
- **Use `opik.project_context()`** when you need temporary project overrides without restructuring code
- **Prefer `@track` decorator** for Python; use low-level SDK when you need full control (async, generators, dynamic spans)
- **Disable tracing in tests** with `OPIK_TRACK_DISABLE=true` or `opik.set_tracing_active(False)`

### Evaluation

- **Don't write test suites upfront** — grow them organically from real production failures
- **Use Test Suites for behavioral regression** (pass/fail) and **Datasets & Metrics for quality benchmarking** (scores)
- **Set `runs_per_item > 1`** for non-deterministic outputs; use `pass_threshold` < `runs_per_item` for tolerance
- **Write assertions in natural language** — be specific: *"Response must cite a step from the provided context"* not *"Response is good"*
- **Use `Hallucination` metric for RAG** — most important metric for grounded responses
- **Use `ContextPrecision` + `ContextRecall` together** to evaluate retrieval quality
- **Use `G-Eval` for custom rubrics** when no pre-built metric fits
- **Run experiments in CI** — compare experiment scores against baseline before merging prompt changes
- **Use `experiment_name` versioning** — e.g., `"rag-v1"`, `"rag-v2"` for side-by-side comparison in dashboard

### Optimization

- **Start with MetaPrompt** for general improvements before trying complex algorithms
- **Use HRPO** when you have systematic failure patterns you want to diagnose
- **Use Few-shot Bayesian** when you have a good example bank and want to find the best subset
- **Always validate optimized prompts** with a held-out test set before deploying

### Production

- **Use Kubernetes** for self-hosted production (not Docker local)
- **Set up Online Evaluation rules** for your most critical quality metrics at launch
- **Use Alerts** to catch regressions before users report them
- **Keep server + SDK versions in sync** — mismatches cause compatibility issues

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Traces not appearing | Run `opik healthcheck`; check `OPIK_API_KEY` and `OPIK_WORKSPACE` |
| 403 errors | Run `opik configure` to reset credentials |
| Short-lived script loses traces | Add `client.flush()` at end of script |
| Traces in wrong project | Check project name resolution order; set `OPIK_PROJECT_NAME` explicitly |
| Debugger info | `Cmd+Shift+.` (Mac) or `Ctrl+Shift+.` (Win/Linux) — shows RTT + version |
| SDK not tracing nested calls | Ensure all functions use `@opik.track`; context propagates automatically within same thread |
| Rate limit exceeded | Batch traces; reduce ingestion frequency; contact Comet for limit increases |

---

## Key Links

- Docs: https://www.comet.com/docs/opik/
- GitHub: https://github.com/comet-ml/opik
- Cloud signup: https://www.comet.com/signup?from=llm
- Full LLM-readable docs: https://www.comet.com/docs/opik/llms-full.txt
- SDK & API reference: https://www.comet.com/docs/opik/ (SDK & API reference tab)
