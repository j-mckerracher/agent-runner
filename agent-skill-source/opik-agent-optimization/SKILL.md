---
name: opik-agent-optimization
version: 1.0.0
description: >
  Opik Agent Optimizer is a turnkey SDK for automatically tuning prompts, tools, and agent workflows.
  Use this skill when optimizing LLM prompts, few-shot examples, tool signatures, or agent behavior using datasets and metrics already logged to Opik.
  Keywords: Opik, agent optimization, prompt optimization, MetaPrompt, FewShot Bayesian, Evolutionary, GEPA, Hierarchical Adaptive, Parameter Optimizer, tool optimization, Optimization Studio, optimize_prompt, optimizer SDK, HRPO, ChatPrompt, Candidate, Metric, Dataset, Optimization Run, optimization algorithm, LLM observability.
---

## When to Use

Activate this skill when:

- Running end-to-end prompt optimization using `opik-optimizer` SDK
- Choosing between optimization algorithms (MetaPrompt, HRPO, Evolutionary, etc.) for your task
- Defining datasets, metrics, and optimization runs for agent or prompt improvement
- Optimizing beyond prompts (tool schemas, MCP function signatures, parameter tuning)
- Using Opik dashboard to review optimization trials and decide whether to ship improved prompts
- Setting up multi-agent optimization, few-shot optimization, or chaining multiple optimizers
- Configuring LLM providers (OpenAI, Anthropic, Gemini, Azure, Ollama, etc.) for optimization

---

## Key Concepts

**ChatPrompt**  
Object containing chat messages with variable placeholders (e.g., `{question}`). Wraps your instruction + user template; required input to all optimizers. See [API Reference](https://www.comet.com/docs/opik/v1/agent_optimization/advanced/api_reference#chatprompt).

**Candidate**  
A concrete prompt (or multi-prompt bundle) proposed by an optimizer for evaluation. Each candidate maps to specific messages, parameters, and tool configurations; all candidates are scored against your metric.

**Metric**  
A function that measures prompt performance. Accepts `dataset_item` (dict) and `llm_output` (string), returns `ScoreResult` or float. Examples: Levenshtein ratio, custom grading logic, automated evaluations.

**Dataset (for Optimization)**  
Collection of input/output pairs (ground truth) used to guide and evaluate optimization. Best practice: split into training (for failure analysis) and validation (for candidate evaluation) datasets.

**Optimization Run**  
Single execution of `optimizer.optimize_prompt(...)`, logged to Opik dashboard under **Evaluation → Optimization runs**. Contains multiple trials and rounds.

**Optimization Trial**  
Evaluation of one candidate against the dataset slice. Each trial logs prompt, score, and reasoning history for analysis.

**Prompt Model**  
LLM that runs your prompt in production (e.g., `gpt-4o-mini`). Set via `ChatPrompt(model="provider/model-name")`.

**Optimizer Model**  
LLM that improves your prompt (typically more capable; e.g., `gpt-4o`). Set via optimizer's `model` parameter.

---

## Algorithms

| Algorithm | Best for | Key input |
|-----------|----------|-----------|
| [MetaPrompt](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/metaprompt_optimizer) | General prompt refinement, wording, structure | Prompt + dataset + metric; supports MCP |
| [HRPO](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/hierarchical_adaptive_optimizer) | Root-cause analysis on complex failures | Metrics with detailed reasons + batches |
| [Few-Shot Bayesian](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/fewshot_bayesian_optimizer) | Optimizing few-shot example counts/order | Dataset with demonstrations; uses Optuna |
| [Evolutionary](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/evolutionary_optimizer) | Discovering diverse prompt structures | Mutation/crossover params; DEAP-based |
| [GEPA](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/gepa_optimizer) | Single-turn reflection-heavy tasks | External GEPA package; Pareto search |
| [Parameter Optimizer](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/parameter_optimizer) | Temperature, top_p, max_tokens tuning | Prompt + parameter search space; Bayesian |

---

## Workflow

### 1. Prepare data & metrics

- **Create dataset**: Upload CSV, use API, or export traces. Split into training + validation datasets.
- **Define metric**: Write function returning `float` or `ScoreResult`. See [Define metrics](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/define_metrics).

### 2. Pick an optimizer

- **General prompt edits** → MetaPrompt
- **Complex failures** → HRPO
- **Few-shot heavy** → Few-Shot Bayesian
- **Diverse exploration** → Evolutionary
- **Parameters only** → Parameter Optimizer

See [selection matrix](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/overview).

### 3. Run optimization

```python
from opik_optimizer import MetaPromptOptimizer, ChatPrompt

prompt = ChatPrompt(
    messages=[
        {"role": "system", "content": "You are a precise assistant."},
        {"role": "user", "content": "{question}"},
    ],
    model="openai/gpt-4o-mini"
)

optimizer = MetaPromptOptimizer(model="openai/gpt-4o")
result = optimizer.optimize_prompt(
    prompt=prompt,
    dataset=dataset,
    metric=metric_fn,
    max_trials=3,
    n_samples=50,  # limit cost during exploration
)
```

### 4. Evaluate & ship

- **Dashboard**: Opik UI → **Evaluation → Optimization runs** shows progress, per-trial traces, prompt diffs.
- **Programmatic**: Compare `result.score` vs. `result.initial_score`; review `result.history` for regressions.
- **Export**: `result.prompt` is the optimized `ChatPrompt` ready for production.

---

## Code Snippets

### Basic optimization

```python
import opik
from opik_optimizer import MetaPromptOptimizer, ChatPrompt

# 1. Create dataset
client = opik.Opik()
dataset = client.get_or_create_dataset(
    name="my-opt-task",
    project_name="my-project"
)
dataset.insert([
    {"question": "What is X?", "answer": "X is Y."},
    {"question": "How does X work?", "answer": "X works by..."},
])

# 2. Define metric
def accuracy(item, output):
    return float(output.strip().lower() == item["answer"].lower())

# 3. Optimize
prompt = ChatPrompt(
    messages=[{"role": "system", "content": "Answer briefly."},
              {"role": "user", "content": "{question}"}],
    model="openai/gpt-4o-mini"
)
optimizer = MetaPromptOptimizer(model="openai/gpt-4o")
result = optimizer.optimize_prompt(
    prompt=prompt,
    dataset=dataset,
    metric=accuracy,
    max_trials=5
)
print(f"Initial: {result.initial_score}, Final: {result.score}")
```

### Multi-prompt optimization

```python
prompts = {
    "researcher": ChatPrompt(
        name="researcher",
        messages=[{"role": "system", "content": "Gather facts and cite sources."},
                  {"role": "user", "content": "{question}"}],
    ),
    "synthesizer": ChatPrompt(
        name="synthesizer",
        messages=[{"role": "system", "content": "Summarize clearly."},
                  {"role": "user", "content": "{question}"}],
    ),
}

optimizer = MetaPromptOptimizer(model="openai/gpt-4o")
result = optimizer.optimize_prompt(
    prompt=prompts,
    dataset=dataset,
    metric=quality,
    max_trials=3,
)
# result.prompt is a dict with same keys; update both agents together
```

---

## Advanced Topics

### Custom metrics with reasoning

Return `ScoreResult` for detailed evaluation explanations:

```python
from opik.evaluation.metrics import ScoreResult

def detailed_metric(item, output):
    passed = output.strip().lower() == item["expected"].lower()
    return ScoreResult(
        score=1.0 if passed else 0.0,
        reason=f"Expected: {item['expected']}, Got: {output}"
    )
```

### Chaining optimizers

Combine sequential optimization (e.g., MetaPrompt → Parameter):

```python
# First: refine prompt text
prompt_result = MetaPromptOptimizer(...).optimize_prompt(...)

# Then: tune sampling params on improved prompt
param_result = ParameterOptimizer(...).optimize_prompt(
    prompt=prompt_result.prompt,
    ...
)
```
See [Chaining optimizers](https://www.comet.com/docs/opik/v1/agent_optimization/advanced/chaining_optimizers).

### Control n_samples

- `n_samples`: number of dataset items per trial (default: full dataset). Use smaller values to reduce cost during exploration.
- `n_samples_strategy`: sampling method (default: `"random_sorted"`).
- `n_samples_minibatch`: for HRPO/GEPA inner-loop evaluations.

### Tool & MCP optimization

Optimize tool descriptions and function schemas alongside prompts. See [Tool optimization](https://www.comet.com/docs/opik/v1/agent_optimization/algorithms/tool_optimization).

### Multimodal optimization

Opik Agent Optimizer supports image/vision tasks. See [Multimodal optimization](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/optimize_multimodal).

### Segment-level optimization

Optimize only specific message segments within a prompt:

```python
from opik_optimizer.utils import prompt_segments

segments = prompt_segments.extract_prompt_segments(my_prompt)
updates = {"message:1": "New user instruction: {user_query}"}
updated = prompt_segments.apply_segment_updates(my_prompt, updates)
```
See [Prompt customization](https://www.comet.com/docs/opik/v1/agent_optimization/advanced/prompt_customization).

---

## Reference

**Setup & Authentication**  
- [Quickstart](https://www.comet.com/docs/opik/v1/agent_optimization/quickstart) – ≤10 min installation
- [Configure models](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/configure_models) – OpenAI, Anthropic, Gemini, Azure, Ollama via LiteLLM

**Core workflows**  
- [Define datasets](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/define_datasets)
- [Define metrics](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/define_metrics)
- [Optimize prompts](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/optimize_prompts)
- [Dashboard results](https://www.comet.com/docs/opik/v1/agent_optimization/optimization/dashboard_results)

**Advanced**  
- [Extending optimizers](https://www.comet.com/docs/opik/v1/agent_optimization/advanced/extending_optimizers)
- [API Reference](https://www.comet.com/docs/opik/v1/agent_optimization/advanced/api_reference)
- [Cookbooks](https://www.comet.com/docs/opik/v1/agent_optimization/cookbooks) – Synthetic data, ARC-AGI, multimodal
- [Optimization Studio (UI)](https://www.comet.com/docs/opik/v1/agent_optimization/optimization_studio) – No-code workflow
- [FAQ](https://www.comet.com/docs/opik/v1/agent_optimization/faq)
- [Known issues](https://www.comet.com/docs/opik/v1/agent_optimization/known_issues)

**Install**: `pip install --upgrade opik opik-optimizer`  
**Authenticate**: `opik configure`
