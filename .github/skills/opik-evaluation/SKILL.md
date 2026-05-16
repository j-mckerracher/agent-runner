---
name: opik-evaluation
version: 1.0.0
description: >
  Opik v1 evaluation framework for building production-grade LLM evaluation pipelines.
  Use this skill when designing experiments, datasets, metrics, LLM-as-a-judge evaluation loops, hallucination detection,
  context recall measurement, agent trajectory tracing, human annotation queues, and feedback scoring systems.
  Keywords: Opik, evaluation, experiments, datasets, metrics, LLM-as-a-judge, hallucination, context recall,
  agent trajectory, annotation queues, heuristic metrics, scoring, evaluation task, dataset versioning, evaluate_threads,
  moderation metric, test suites, regression testing.
---

# Opik Evaluation

Opik is a platform for evaluating and monitoring LLM applications at scale. The evaluation system lets you systematically test LLM outputs, agents, and multi-turn interactions against quantitative and qualitative metrics.

## When to Use This Skill

Activate this skill when:

- Designing evaluation frameworks for LLM applications, agents, or multi-turn systems
- Creating test datasets, managing versioning, and tracking data lineage
- Running prompt experiments or A/B tests on sample datasets
- Implementing custom evaluation metrics (heuristic or LLM-as-judge)
- Detecting hallucinations, measuring context recall, or scoring agent trajectories
- Setting up human-in-the-loop workflows with annotation queues
- Organizing and collecting structured feedback from subject matter experts (SMEs)
- Evaluating multimodal inputs (text, image, audio) end-to-end
- Logging experiment results and comparing model performance

---

## Key Concepts

**Dataset**: Immutable versioned collection of test cases (input, expected_output, and custom fields). Datasets track all modifications as versions for reproducibility.

**Experiment**: A single evaluation run on a dataset with a specific model/prompt and metrics. Results are scored and aggregated per item.

**Evaluation Task**: Function that accepts a dataset item and returns a dict of fields (e.g., `output`, `reasoning`) to score. Defines how your application processes each test case.

**Metrics**: Scoring functions that evaluate output quality. Two categories:
- **Heuristic metrics**: Deterministic (Equals, Contains, BERTScore, BLEU, ChrF, RegexMatch, IsJson)
- **LLM-as-Judge metrics**: Delegate scoring to an LLM for semantic judgment (Factuality, Coherence, Custom LLM prompts)

**Agent Trajectory**: Complete trace of an agent's steps—tool selection, reasoning, intermediate results. Evaluate decision quality, not just final output.

**Annotation Queue**: Collection of traces or threads for human review. SMEs annotate outputs and collect structured feedback without technical setup.

**Project Scope**: In Opik 2.0, datasets and experiments are project-scoped. Always specify `project_name` when creating resources.

---

## Common Tasks

### Manage Datasets
- **Create dataset via UI**: Navigate Evaluation > Datasets, upload CSV (1,000 rows max)
- **Create via SDK**: Programmatic dataset creation with versioning
- **Reference**: [manage_datasets](/v1/evaluation/manage_datasets)

### Evaluate a Single Prompt
- Test multiple prompts on the same dataset
- Use prompt playground for UI-based comparison or `evaluate_prompt()` in SDK
- **Reference**: [evaluate_prompt](/v1/evaluation/evaluate_prompt)

### Evaluate LLM Applications & Agents
- Add tracing to application with `@track` decorator and `track_openai()` wrapper
- Define `EvaluationTask` that processes each dataset item
- Run experiments with chosen metrics
- **Reference**: [evaluate_your_llm](/v1/evaluation/evaluate_your_llm)

### Evaluate Agent Trajectories
- Assess agent decision quality, tool selection, reasoning steps—not just final output
- Trace agent with Opik observability, then evaluate trajectory steps
- Use metrics like hallucination, tool success rate, reasoning coherence
- **Reference**: [evaluate_agent_trajectory](/v1/evaluation/evaluate_agent_trajectory)

### Set Up Annotation Queues
- Create queue with instructions and feedback definitions
- Add traces/threads from lists or detail views
- Share queue link with SMEs for non-technical annotation
- **Reference**: [annotation_queues](/v1/evaluation/annotation_queues)

---

## Evaluation Best Practices

1. **Build evaluations from real failures.** The newer Opik evaluation workflow recommends turning bad production traces into test cases, instead of trying to design a perfect suite up front.
2. **Choose the right evaluation mode.**
   - Use **test suites** for behavioral pass/fail checks and regression gates.
   - Use **datasets and metrics** for benchmark-style numeric scoring across many examples.
3. **Always scope work to a project.** In newer Opik docs, datasets and experiments are project-scoped, so include `project_name` consistently when creating datasets, experiments, and evaluations.
4. **Evaluate multi-turn systems at the thread level.** For conversational agents, prefer `evaluate_threads()` when you need to score complete dialogues rather than isolated turns. Use `trace_input_transform` and `trace_output_transform` to normalize framework-specific trace structures.
5. **Use OQL filters to target the right threads.** Start with a narrow `filter_string` so thread evaluation runs on stable, relevant conversations instead of every trace in a project.
6. **Automate recurring thread evaluation.** The thread evaluation guide explicitly recommends scheduled evaluations for selected traces/threads so feedback scores stay fresh over time.
7. **Treat moderation as a calibrated metric, not a binary oracle.** The moderation metric returns a `0-1` safety score plus reasoning. Set thresholds intentionally, review borderline outputs, and back-test against a representative moderation dataset before enforcing hard gates.
8. **Pair automated scoring with human review where risk is high.** Use annotation queues and multi-reviewer feedback for moderation, policy compliance, and nuanced conversation quality.
9. **Compare experiments, not anecdotes.** Use experiments to compare prompts, models, or tool changes side-by-side so you can see whether a fix improved overall quality or just one example.

### Thread Evaluation Pattern

Use this when evaluating chatbots, copilots, or any agent with multi-turn memory:

```python
from opik.evaluation import evaluate_threads
from opik.evaluation.metrics import ConversationalCoherenceMetric, UserFrustrationMetric

results = evaluate_threads(
    project_name="support-bot",
    eval_project_name="support-bot-evals",
    filter_string='thread_id contains "prod-"',
    metrics=[
        ConversationalCoherenceMetric(),
        UserFrustrationMetric(),
    ],
    trace_input_transform=lambda x: x["input"],
    trace_output_transform=lambda x: x["output"],
)
```

### Moderation Metric Pattern

Use this when you need a safety-oriented score and explanation for generated text:

```python
from opik.evaluation.metrics import Moderation

metric = Moderation()
score = metric.score(output="Generated assistant response")

is_flagged = score.value >= 0.5
reason = score.reason
```

---

## Code Snippets

### Python: Create Dataset and Run Evaluation

```python
import opik
from opik import track
from opik.integrations.openai import track_openai

# Initialize Opik
client = opik.Opik(project_name="my-project")

# Create dataset
dataset = client.get_or_create_dataset(name="my_test_data")
dataset.insert([
    {"input": "What is 2+2?", "expected_output": "4"},
    {"input": "Translate to Spanish: hello", "expected_output": "hola"},
])

# Define evaluation task
@track
def evaluate_item(item):
    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": item["input"]}],
    )
    return {"output": response.choices[0].message.content}

# Run experiment with metrics
opik.evaluate(
    dataset=dataset,
    task=evaluate_item,
    metrics=[
        opik.Equals(name="exact_match"),
        opik.LlmJudge(name="factuality"),
    ],
    project_name="my-project",
)
```

### TypeScript: Evaluate a Prompt

```typescript
import { Opik, evaluatePrompt } from 'opik';

const opikClient = new Opik();
const dataset = await opikClient.getOrCreateDataset({
    name: "qa_dataset",
});

await evaluatePrompt({
    dataset,
    messages: [
        { role: "user", content: "Q: {{input}}\nExpected: {{expected}}" },
    ],
    model: "gpt-4o",
    projectName: "my-project",
});
```

---

## Reference

| Resource | URL |
| --- | --- |
| Manage Datasets | https://www.comet.com/docs/opik/v1/evaluation/manage_datasets |
| Evaluate Prompts | https://www.comet.com/docs/opik/v1/evaluation/evaluate_prompt |
| Evaluate LLM Apps | https://www.comet.com/docs/opik/v1/evaluation/evaluate_your_llm |
| Agent Trajectories | https://www.comet.com/docs/opik/v1/evaluation/evaluate_agent_trajectory |
| Metrics Overview | https://www.comet.com/docs/opik/v1/evaluation/metrics/overview |
| Annotation Queues | https://www.comet.com/docs/opik/v1/evaluation/annotation_queues |
| Multimodal Evaluation | https://www.comet.com/docs/opik/v1/evaluation/evaluate_multimodal |
| Multi-Turn Agents | https://www.comet.com/docs/opik/v1/evaluation/evaluate_multi_turn_agents |
| REST API Logging | https://www.comet.com/docs/opik/v1/evaluation/log_experiments_with_rest_api |
| Evaluation Overview | https://www.comet.com/docs/opik/evaluation/overview |
| Evaluation Concepts | https://www.comet.com/docs/opik/evaluation/concepts |
| Evaluate Threads | https://www.comet.com/docs/opik/evaluation/evaluate_threads |
| Moderation Metric Guide | https://www.comet.com/docs/opik/evaluation/evaluate_moderation_metric |
| Moderation Metric Reference | https://www.comet.com/docs/opik/evaluation/metrics/moderation |
