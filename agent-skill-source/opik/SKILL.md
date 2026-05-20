---
name: opik
description: |
  Comprehensive Opik LLM observability, tracing, and evaluation skill for the agent-runner pipeline.
  Use this skill when:
  - Instrumenting a new pipeline stage with Opik traces or spans
  - Adding structured evaluation metrics to an eval-optimizer loop
  - Building or populating Opik datasets from synthetic fixtures
  - Running opik.evaluate() experiments to benchmark agent quality
  - Creating custom LLM-as-judge metrics for agent stages
  - Filling observability gaps (CLI agent calls, artifact I/O, thread grouping)
  - Configuring online evaluation, alerts, or experiment tracking
  - Referencing the full metrics catalogue or API surface
  Primary codebase files: opik_integration.py, steps.py, evaluator_optimizer_loops.py
---

# Opik — Agent-Runner Integration Guide

Opik is an open-source platform (by Comet) for logging, debugging, and evaluating LLM applications and AI agent pipelines. This skill is scoped to the **agent-runner** codebase, which runs a 6-stage multi-agent workflow (intake → task-gen → task-assignment → implementation → QA → lessons) and uses Opik for tracing and evaluation.

Official docs: https://www.comet.com/docs/opik/  
GitHub: https://github.com/comet-ml/opik

---

## Table of Contents

1. [Installation & Configuration](#1-installation--configuration)
2. [Core Concepts Mapped to Agent-Runner](#2-core-concepts-mapped-to-agent-runner)
3. [Tracing Reference (Full API)](#3-tracing-reference-full-api)
4. [Anthropic SDK Integration](#4-anthropic-sdk-integration)
5. [Structured Evaluation: From "PASS" Strings to Scored Metrics](#5-structured-evaluation-from-pass-strings-to-scored-metrics)
6. [Metrics Catalogue](#6-metrics-catalogue)
7. [Custom Metrics](#7-custom-metrics)
8. [Dataset Management](#8-dataset-management)
9. [Running Experiments (opik.evaluate)](#9-running-experiments-opikevaluate)
10. [Test Suites (Behavioral Regression)](#10-test-suites-behavioral-regression)
11. [Gap-Filling: CLI Agent Tracing](#11-gap-filling-cli-agent-tracing)
12. [Gap-Filling: Artifact Event Tracking](#12-gap-filling-artifact-event-tracking)
13. [Thread ID Convention for change_id Grouping](#13-thread-id-convention-for-change_id-grouping)
14. [Eval-Optimizer Loop Integration](#14-eval-optimizer-loop-integration)
15. [Production Observability](#15-production-observability)
16. [Anti-Patterns](#16-anti-patterns)
17. [Quick Reference Card](#17-quick-reference-card)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. Installation & Configuration

### Package

```bash
pip install opik          # already in requirements.txt:67
```

### Environment Variables

Set in your shell or `.env` before running `run.py`:

```bash
# Required for SDK evaluators (call_evaluator_sdk in opik_integration.py)
ANTHROPIC_API_KEY=sk-ant-...

# Required for Opik Cloud; omit when using a self-hosted local instance
OPIK_API_KEY=<your-opik-api-key>

# Optional (Cloud only) — your workspace slug
OPIK_WORKSPACE=<workspace-name>

# Optional — self-hosted instance URL
OPIK_URL_OVERRIDE=http://localhost:5173/api

# Optional — project name shown in dashboard (default already set in opik_integration.py:34)
OPIK_PROJECT_NAME=agent-runner

# Disable tracing entirely (useful in unit tests)
OPIK_TRACK_DISABLE=true
```

### How the Project Name Is Set

`opik_integration.py:34` sets the default project name before any decorator fires:

```python
os.environ.setdefault("OPIK_PROJECT_NAME", "agent-runner")
```

The resolution order (highest priority first): explicit `project_name=` argument → `Opik(project_name=...)` → `OPIK_PROJECT_NAME` env var → `"Default Project"`.

### Self-Hosted Docker (Local Dev)

```bash
git clone https://github.com/comet-ml/opik.git
cd opik
./opik.sh          # starts at http://localhost:5173
# Then set:
export OPIK_URL_OVERRIDE=http://localhost:5173/api
```

### Interactive Setup

```bash
opik configure       # interactive: sets API key + workspace
opik healthcheck     # verify connectivity to backend
```

---

## 2. Core Concepts Mapped to Agent-Runner

| Opik Concept | Definition | Agent-Runner Mapping |
|---|---|---|
| **Trace** | Complete execution path for one logical operation | One pipeline stage (e.g., `"intake"`, `"task-gen-producer"`) |
| **Span** | Individual sub-operation within a trace | One LLM call, one artifact read/write, one CLI invocation |
| **Thread** | Group of related traces for a conversation | All traces for a single `change_id` run |
| **Dataset** | Collection of test inputs (+ optional expected outputs) | Synthetic fixture files in `tests/fixtures/` |
| **Experiment** | One evaluation run: dataset → task fn → metrics → scores | A benchmark run comparing `task-gen-v1` vs `task-gen-v2` |
| **Test Suite** | LLM-judged pass/fail behavioral assertions | Regression checks: "tasks.yaml must have priority field" |
| **Feedback Score** | Numeric annotation on a trace (0–1) | `1.0` when evaluator returns PASS, `0.0` when FAIL |
| **Optimization Run** | Automated prompt refinement | Tuning evaluator prompts in `agent-sources/` |

### Span Types

| Type | Use |
|---|---|
| `general` | Default; any non-LLM operation |
| `tool` | External tool or subprocess calls (e.g., `run_agent_cmd`) |
| `llm` | Direct LLM API calls (use on `call_evaluator_sdk`) |
| `guardrail` | Safety/content policy checks |

---

## 3. Tracing Reference (Full API)

### 3.1 `@opik.track` Decorator (recommended for Python functions)

```python
import opik

@opik.track
def my_function(input_value: str) -> str:
    # inputs + outputs are auto-logged; nested @track calls become child spans
    return result

# With explicit options:
@opik.track(
    name="my-custom-name",          # overrides function name in dashboard
    project_name="agent-runner",    # sets Opik project
    type="llm",                     # span type: general | tool | llm | guardrail
    flush=True,                     # flush after each call (use in short-lived scripts)
)
def my_llm_call(prompt: str) -> str: ...
```

**Key behavior:** When a `@track`-decorated function calls another `@track`-decorated function, the inner call automatically becomes a child span. No extra wiring needed.

### 3.2 `opik.start_as_current_trace` Context Manager

This is the pattern already used in `steps.py`. Use it when you need explicit control over trace properties.

```python
import opik

with opik.start_as_current_trace("stage-name", project_name="agent-runner") as trace:
    trace.input = {"key": "value"}           # dict, logged as trace input
    trace.output = {"result": "..."}          # dict, logged as trace output
    trace.tags = ["intake", "synthetic"]      # list of string tags
    trace.metadata = {"change_id": "abc123"}  # arbitrary metadata dict
    trace.thread_id = "abc123"               # groups trace into a Thread
    # ... do work ...
    trace.output = {"stdout_preview": result[:2000]}   # update output at end
```

### 3.3 `opik.start_as_current_span` for Nested Spans

Use inside an active trace to record sub-operations:

```python
with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
    trace.input = {...}
    
    # Nested span for the CLI invocation
    with opik.start_as_current_span("run-intake-agent", type="tool") as span:
        span.input = {"agent": "intake-agent", "prompt_preview": prompt[:300]}
        result = run_agent_cmd(runner=runner, prompt=prompt, agent="intake-agent")
        span.output = {"stdout": result[:2000], "exit_ok": True}
    
    trace.output = {"stdout_preview": result[:2000]}
```

### 3.4 Runtime Context Updates

Update trace or span properties from inside deeply nested code:

```python
import opik

# Update the innermost active trace
opik.opik_context.update_current_trace(
    tags=["production"],
    metadata={"change_id": change_id, "iteration": 2},
    thread_id=change_id,
    feedback_scores=[{"name": "pass", "value": 1.0}],
)

# Update the innermost active span
opik.opik_context.update_current_span(
    metadata={"artifact_path": "agent-context/abc/planning/tasks.yaml"},
    usage={"prompt_tokens": 1200, "completion_tokens": 400, "total_tokens": 1600},
)
```

### 3.5 Per-Call `opik_args` Overrides

Pass trace/span properties at call-site without modifying the function:

```python
result = my_tracked_function(
    "input",
    opik_args={
        "trace": {
            "thread_id": change_id,
            "metadata": {"user_id": "pipeline"},
            "tags": ["stage:intake"],
        },
        "span": {
            "metadata": {"model": "claude-haiku-4-5-20251001"},
        },
    }
)
```

### 3.6 Low-Level SDK (Full Control)

Use when you need async, generators, or full manual control:

```python
from opik import Opik

client = Opik(project_name="agent-runner")

trace = client.trace(
    name="intake",
    input={"intake_source": intake_source, "change_id": change_id},
    output={"stdout_preview": result[:2000]},
    tags=["intake"],
    metadata={"change_id": change_id},
    thread_id=change_id,
)

span = trace.span(
    name="run-intake-agent",
    type="tool",
    input={"agent": "intake-agent"},
    output={"stdout": result[:2000]},
)

trace.end()
client.flush()    # REQUIRED for short-lived scripts
```

### 3.7 Flush & Tracing Control

```python
client.flush()                          # ensure delivery before process exits
opik.set_tracing_active(False)          # disable for a section of code
opik.set_tracing_active(True)           # re-enable
opik.is_tracing_active()                # check current state

# In tests: disable globally via env var
os.environ["OPIK_TRACK_DISABLE"] = "true"
```

### 3.8 Project Context Override

Temporarily route traces to a different project:

```python
with opik.project_context("agent-runner-ci"):
    run_integration_test()
```

---

## 4. Anthropic SDK Integration

### 4.1 Existing Pattern in This Codebase

`opik_integration.py:86` wraps the evaluator LLM call:

```python
@opik.track(name="sdk-evaluator", type="llm")
def call_evaluator_sdk(context: str, agent_name: str, model: str = "claude-haiku-4-5-20251001") -> str:
    ...
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
```

Opik automatically records: model name, token counts, latency, input/output content.

### 4.2 Native Anthropic Integration (Alternative)

Use the native integration to auto-trace **all** calls through an Anthropic client:

```python
import anthropic
from opik.integrations.anthropic import track_anthropic

client = track_anthropic(anthropic.Anthropic())
# Every call via this client is now traced automatically, including token usage
response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=4096,
    system=system_prompt,
    messages=[{"role": "user", "content": user_message}],
)
```

### 4.3 Differentiating Evaluator Spans

The current `call_evaluator_sdk()` always logs as `"sdk-evaluator"`. To distinguish which stage called it, pass a per-call name:

```python
# Option A: Separate named wrapper functions
@opik.track(name="task-plan-evaluator-llm", type="llm")
def call_task_plan_evaluator(context: str) -> str:
    return _sdk_call(context, "task-plan-evaluator")

@opik.track(name="assignment-evaluator-llm", type="llm")
def call_assignment_evaluator(context: str) -> str:
    return _sdk_call(context, "assignment-evaluator")

# Option B: Use opik_args at call-site
result = call_evaluator_sdk(
    context, "task-plan-evaluator",
    opik_args={"span": {"name": "task-plan-evaluator-llm"}}
)
```

### 4.4 Token Usage Logging

When using the low-level SDK, log token counts explicitly:

```python
response = client.messages.create(...)
opik.opik_context.update_current_span(
    usage={
        "prompt_tokens": response.usage.input_tokens,
        "completion_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
    }
)
```

---

## 5. Structured Evaluation: From "PASS" Strings to Scored Metrics

### 5.1 The Current Gap

In `evaluator_optimizer_loops.py:47`, the loop checks:
```python
if "PASS" in evaluator_out:
    break
```

This is a raw string check — no structured score is logged to Opik. The dashboard shows the evaluator output as text with no queryable metric.

### 5.2 Enhanced Pattern: Log Feedback Scores

After calling `call_evaluator_sdk()`, parse the result and log a feedback score to the active trace:

```python
# In steps.py, after the call_evaluator_sdk() result is obtained:
result = call_evaluator_sdk(context, "task-plan-evaluator")
passed = "PASS" in result

opik.opik_context.update_current_trace(
    feedback_scores=[
        {"name": "evaluator_pass", "value": 1.0 if passed else 0.0},
    ],
    metadata={
        "evaluator": "task-plan-evaluator",
        "passed": passed,
    }
)
```

### 5.3 Logging Iteration Metrics in the Eval-Optimizer Loop

In `evaluator_optimizer_loops.py`, enrich each iteration:

```python
for i in range(iter_count):
    producer_out = producer_func(combined_input, runner=runner)
    evaluator_out = evaluator_func(evaluator_prompt, runner=runner)
    passed = "PASS" in evaluator_out

    # Log iteration score to the active trace (if one is open)
    opik.opik_context.update_current_trace(
        feedback_scores=[
            {"name": "pass", "value": 1.0 if passed else 0.0},
            {"name": "iteration_count", "value": float(i + 1)},
        ],
        metadata={"iteration": i + 1, "early_stop": passed},
    )

    if passed:
        break
```

### 5.4 Using `ScoreResult` in Custom Metrics

When writing a custom metric class, return a `ScoreResult`:

```python
from opik.evaluation.metrics import base_metric, score_result

class EvalOptimizerPassRate(base_metric.BaseMetric):
    """Scores whether an eval-optimizer loop converged and how quickly."""

    def score(self, output: str, **kwargs) -> score_result.ScoreResult:
        passed = "PASS" in output
        return score_result.ScoreResult(
            value=1.0 if passed else 0.0,
            name=self.name,
            reason="Evaluator returned PASS" if passed else "Evaluator returned FAIL",
        )
```

---

## 6. Metrics Catalogue

### 6.1 Heuristic Metrics (Deterministic — No LLM Cost)

Import from `opik.evaluation.metrics`:

| Metric Class | What It Checks | Agent-Runner Use Case |
|---|---|---|
| `Equals` | Exact string match | Validate literal field values |
| `Contains` | Substring presence | Check `"PASS"` in evaluator output |
| `RegexMatch` | Regex pattern match | Validate change_id format, YAML keys |
| `IsJson` | Output is valid JSON | Validate `assignments.json` output |
| `ROUGE` | Recall-Oriented Understudy for Gisting (overlap) | Compare generated tasks against reference |
| `BERTScore` | Semantic similarity (embedding-based) | Fuzzy match implementation report vs spec |
| `Levenshtein` | Edit distance | Detect near-duplicate task descriptions |
| `BLEU` | N-gram precision against reference | Compare QA reports across runs |
| `Readability` | Flesch-Kincaid readability | Check that constraints.md is human-readable |

```python
from opik.evaluation.metrics import IsJson, Contains, RegexMatch

is_json = IsJson()
score = is_json.score(output=json_string)   # ScoreResult(value=True/False)

contains_pass = Contains(substring="PASS")
score = contains_pass.score(output=evaluator_result)

change_id_pattern = RegexMatch(regex=r"^[a-z0-9\-]{6,40}$")
score = change_id_pattern.score(output=change_id)
```

### 6.2 LLM-as-Judge Metrics (Semantic — LLM Cost)

All LLM metrics default to GPT-4o nano unless overridden. Override with:

```python
metric = Hallucination(model="claude-haiku-4-5-20251001")
```

| Metric Class | Inputs Required | Agent-Runner Use Case |
|---|---|---|
| `Hallucination` | `input`, `output`, `context` | Verify impl report claims match the UoW spec |
| `AnswerRelevance` | `input`, `output` | Check QA report addresses acceptance criteria |
| `ContextPrecision` | `input`, `output`, `expected_output` | RAG precision (if retrieval is added) |
| `ContextRecall` | `input`, `output`, `expected_output` | RAG recall (if retrieval is added) |
| `Moderation` | `output` | Ensure outputs don't contain harmful content |
| `G-Eval` | `output` (+ custom criteria) | Any stage with custom rubric |
| `MeaningMatch` | `output`, `reference` | Compare task plans across agent versions |
| `AgentTaskCompletionJudge` | `input`, `output` | **Implementation eval**: did engineer complete UoW? |
| `AgentToolCorrectnessJudge` | `input`, `output` | **Implementation eval**: were correct tools used? |
| `QARelevanceJudge` | `input`, `output` | **QA eval**: does report address the user story? |
| `SummarizationCoherenceJudge` | `output` | **Lessons eval**: is lessons report coherent? |
| `SummarizationConsistencyJudge` | `output`, `context` | **Lessons eval**: is report consistent with artifacts? |
| `DialogueHelpfulnessJudge` | `input`, `output` | General helpfulness assessment |
| `ComplianceRiskJudge` | `output` | Flag non-compliant statements |
| `LLMJuriesJudge` | varies | Consensus scoring from multiple LLM judges |

#### AgentTaskCompletionJudge (Most Relevant for Implementation Eval)

```python
from opik.evaluation.metrics import AgentTaskCompletionJudge

metric = AgentTaskCompletionJudge(model="claude-haiku-4-5-20251001")
score = metric.score(
    input="Implement UoW-001: Add validation to the login form per uow_spec.yaml",
    output=impl_report_content,    # content of impl_report.yaml
)
# Returns ScoreResult(value=0.0–1.0, reason="...")
```

#### AgentToolCorrectnessJudge

```python
from opik.evaluation.metrics import AgentToolCorrectnessJudge

metric = AgentToolCorrectnessJudge(model="claude-haiku-4-5-20251001")
score = metric.score(
    input="Create tasks.yaml with decomposed tasks from the user story",
    output=task_gen_stdout,        # stdout from task-generator agent
)
```

#### Hallucination (For Implementation Reports)

```python
from opik.evaluation.metrics import Hallucination

metric = Hallucination(model="claude-haiku-4-5-20251001")
score = metric.score(
    input="UoW-001: Add validation to login form",
    output=impl_report_yaml_content,
    context=uow_spec_yaml_content,    # ground truth context
)
# value near 0.0 = no hallucination; near 1.0 = high hallucination
```

#### G-Eval (Custom Rubric — Most Flexible)

```python
from opik.evaluation.metrics import GEval

task_plan_quality = GEval(
    task_introduction=(
        "You are evaluating a task decomposition plan produced by a task-generator AI agent. "
        "The plan is a YAML file listing tasks derived from a user story."
    ),
    evaluation_criteria=(
        "1. Every task has a unique ID, title, description, and priority field. "
        "2. Tasks are atomic — each addresses a single concern. "
        "3. Dependencies are explicitly listed and form a valid DAG (no cycles). "
        "4. All acceptance criteria from the story are covered by at least one task. "
        "5. No task is duplicated or trivially similar to another."
    ),
    model="claude-haiku-4-5-20251001",
)

payload = f"""
STORY:
{story_yaml_content}

TASKS OUTPUT:
{tasks_yaml_content}
"""

score = task_plan_quality.score(output=payload)
# Returns ScoreResult(value=0.0–1.0, reason="...")
```

---

## 7. Custom Metrics

### 7.1 Base Pattern

Subclass `BaseMetric` and implement `score()`:

```python
from opik.evaluation.metrics import base_metric, score_result

class TaskPlanQualityMetric(base_metric.BaseMetric):
    """
    Validates task plan structure deterministically (no LLM cost).
    Checks: required fields present, non-empty tasks list, valid priorities.
    """

    REQUIRED_FIELDS = {"id", "title", "description", "priority"}
    VALID_PRIORITIES = {"high", "medium", "low"}

    def __init__(self, name: str = "task_plan_quality"):
        super().__init__(name)

    def score(
        self,
        output: str,
        **ignored_kwargs,
    ) -> list[score_result.ScoreResult]:
        import yaml

        try:
            plan = yaml.safe_load(output)
        except Exception as e:
            return [score_result.ScoreResult(
                value=0.0, name=self.name,
                reason=f"YAML parse error: {e}"
            )]

        tasks = plan.get("tasks", []) if isinstance(plan, dict) else plan
        if not tasks:
            return [score_result.ScoreResult(
                value=0.0, name=self.name, reason="tasks list is empty"
            )]

        missing_fields = []
        invalid_priorities = []
        for t in tasks:
            missing = self.REQUIRED_FIELDS - set(t.keys())
            if missing:
                missing_fields.append((t.get("id", "?"), missing))
            if t.get("priority", "").lower() not in self.VALID_PRIORITIES:
                invalid_priorities.append(t.get("id", "?"))

        issues = []
        if missing_fields:
            issues.append(f"Missing fields: {missing_fields}")
        if invalid_priorities:
            issues.append(f"Invalid priorities: {invalid_priorities}")

        value = 1.0 if not issues else 0.0
        return [
            score_result.ScoreResult(
                value=value,
                name=self.name,
                reason="; ".join(issues) if issues else "All required fields present",
            ),
            score_result.ScoreResult(
                value=float(len(tasks)),
                name="task_count",
                reason=f"{len(tasks)} tasks in plan",
            ),
        ]
```

### 7.2 LLM-as-Judge Custom Metric with LiteLLM

Use `LiteLLMChatModel` for provider-agnostic LLM calls within a metric:

```python
import json
from pydantic import BaseModel
from opik.evaluation import models
from opik.evaluation.metrics import base_metric, score_result


class AssignmentScheduleMetric(base_metric.BaseMetric):
    """
    LLM-based metric that evaluates whether an execution schedule is
    parallelizable, respects dependencies, and has reasonable batch sizes.
    """

    PROMPT = """Evaluate this execution schedule for a software engineering pipeline.

ASSIGNMENTS JSON:
{assignments}

Score on a scale of 0-10 (10 = excellent) and explain:
1. Are tasks that can run in parallel grouped in the same batch?
2. Do task dependencies between batches make logical sense?
3. Is no single batch overloaded (more than 5 tasks)?

Return JSON: {{"score": int, "reason": str}}"""

    def __init__(self, name: str = "assignment_schedule", model_name: str = "claude-haiku-4-5-20251001"):
        super().__init__(name)
        self.llm = models.LiteLLMChatModel(model_name=model_name)

    def score(self, output: str, **kwargs) -> score_result.ScoreResult:
        prompt = self.PROMPT.format(assignments=output[:3000])  # trim to avoid token overflow

        class Result(BaseModel):
            score: int
            reason: str

        response = self.llm.generate_string(input=prompt, response_format=Result)
        data = json.loads(response)
        return score_result.ScoreResult(
            value=data["score"] / 10.0,   # normalize to 0.0–1.0
            name=self.name,
            reason=data["reason"],
        )
```

### 7.3 Per-Stage Custom Metric Stubs

Ready-to-extend templates for each evaluator stage:

```python
class ImplementationCorrectnessMetric(base_metric.BaseMetric):
    """Checks impl_report.yaml claims against uow_spec.yaml using Hallucination."""
    def score(self, output: str, context: str = "", **kwargs) -> score_result.ScoreResult:
        from opik.evaluation.metrics import Hallucination
        return Hallucination(model="claude-haiku-4-5-20251001").score(
            input="Implement the UoW as specified",
            output=output,
            context=context,
        )


class QAReportCompletenessMetric(base_metric.BaseMetric):
    """Checks whether qa_report.yaml covers all acceptance criteria."""
    def score(self, output: str, expected_output: str = "", **kwargs) -> score_result.ScoreResult:
        from opik.evaluation.metrics import MeaningMatch
        return MeaningMatch(model="claude-haiku-4-5-20251001").score(
            output=output,
            reference=expected_output,
        )
```

---

## 8. Dataset Management

### 8.1 Create / Get a Dataset

```python
import opik

client = opik.Opik()

# Creates if missing; retrieves if exists
dataset = client.get_or_create_dataset(name="task-gen-eval")

# Or with description
dataset = client.get_or_create_dataset(
    name="task-gen-eval",
    description="Task generation evaluation dataset from synthetic fixtures",
)
```

### 8.2 Dataset Item Schema

Each item is a dict. Common convention:

```python
{
    "input":           {...},    # the agent's input context
    "expected_output": {...},    # what a passing output looks like (or schema)
    # Add any extra fields your scoring metrics need to access
    "story_id":        "...",
    "fixture_path":    "...",
}
```

### 8.3 Inserting Items from Synthetic Fixtures

Build a dataset from the fixtures in `tests/`:

```python
import yaml
from pathlib import Path
import opik

FIXTURES_DIR = Path("tests/fixtures")

client = opik.Opik()
dataset = client.get_or_create_dataset("task-gen-eval")

items = []
for fixture_path in FIXTURES_DIR.glob("*.yaml"):
    story = yaml.safe_load(fixture_path.read_text())
    items.append({
        "input": {
            "story_id":        story.get("change_id"),
            "story_yaml":      fixture_path.read_text(),
            "acceptance_criteria": story.get("acceptance_criteria", []),
        },
        "expected_output": {
            "schema_requirements": [
                "tasks key present",
                "each task has id, title, description, priority",
                "at least one task per acceptance criterion",
            ],
        },
        "fixture_path": str(fixture_path),
    })

dataset.insert(items)
print(f"Inserted {len(items)} items into task-gen-eval dataset")
```

### 8.4 Per-Stage Dataset Design

| Dataset Name | Input Fields | Expected Output Fields | Notes |
|---|---|---|---|
| `task-gen-eval` | `story_yaml`, `acceptance_criteria` | `schema_requirements` | Use `TaskPlanQualityMetric` |
| `assignment-eval` | `tasks_yaml` | `parallelization_requirements` | Use `AssignmentScheduleMetric` |
| `implementation-eval` | `uow_spec_yaml`, `impl_report_yaml` | `correctness_criteria` | Use `AgentTaskCompletionJudge` + `Hallucination` |
| `qa-eval` | `story_yaml`, `impl_report_yaml`, `qa_report_yaml` | `coverage_criteria` | Use `QAReportCompletenessMetric` |

### 8.5 Dataset Versioning

Name datasets with a version suffix when making breaking changes:

```python
dataset_v2 = client.get_or_create_dataset("task-gen-eval-v2")
```

Or add a date for time-series comparison:

```python
from datetime import date
dataset = client.get_or_create_dataset(f"task-gen-eval-{date.today():%Y%m%d}")
```

---

## 9. Running Experiments (`opik.evaluate`)

### 9.1 Basic Experiment

```python
from opik.evaluation import evaluate
from opik.evaluation.metrics import AgentTaskCompletionJudge, Hallucination
import opik

client = opik.Opik()
dataset = client.get_or_create_dataset("implementation-eval")


def task(item: dict) -> dict:
    """
    Run the implementation evaluator against one dataset item.
    In production this would call call_evaluator_sdk(); here we return stored output.
    """
    return {
        "output": item["input"].get("impl_report_yaml", ""),
        "context": item["input"].get("uow_spec_yaml", ""),
    }


evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[
        AgentTaskCompletionJudge(model="claude-haiku-4-5-20251001"),
        Hallucination(model="claude-haiku-4-5-20251001"),
    ],
    experiment_name="impl-eval-haiku-v1",
    experiment_config={
        "model":          "claude-haiku-4-5-20251001",
        "evaluator_agent": "implementation-evaluator",
        "pipeline_version": "v1.2",
    },
    nb_samples=None,        # None = run all items; set an int to subsample
)
```

### 9.2 Task Function Signature

The `task` function receives one dataset item dict. It must return a dict. Keys returned become available to scoring metrics:

```python
def task(item: dict) -> dict:
    # item contains all keys from the dataset item
    story_yaml  = item["input"]["story_yaml"]
    
    # Call your actual pipeline function here
    tasks_yaml  = call_task_generator(story_yaml)
    
    return {
        "output":   tasks_yaml,        # mandatory — passed to all metrics as `output`
        "context":  story_yaml,        # available to metrics that accept `context`
    }
```

### 9.3 Experiment Naming Convention

```
{stage}-{model_short}-v{N}

Examples:
  task-gen-haiku-v1
  task-gen-haiku-v2        # after prompt change
  impl-eval-haiku-v1
  qa-eval-haiku-v1
  full-pipeline-v3         # end-to-end experiment
```

### 9.4 Comparing Experiments in CI

Run experiments in CI and fail if average score drops below a threshold:

```python
from opik.evaluation import evaluate

result = evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[AgentTaskCompletionJudge(model="claude-haiku-4-5-20251001")],
    experiment_name=f"impl-eval-haiku-{os.environ.get('GIT_SHA', 'local')[:7]}",
)

# Access results
scores = [item.score for item in result.test_results]
avg = sum(scores) / len(scores)

BASELINE_THRESHOLD = 0.75
if avg < BASELINE_THRESHOLD:
    raise ValueError(
        f"Experiment avg score {avg:.2f} < threshold {BASELINE_THRESHOLD}. "
        "Prompt change may have regressed quality."
    )
```

### 9.5 Sampling for Speed

Run on a random subset during development:

```python
evaluate(
    dataset=dataset,
    task=task,
    scoring_metrics=[...],
    experiment_name="quick-check",
    nb_samples=5,     # evaluate 5 random items only
)
```

---

## 10. Test Suites (Behavioral Regression)

Test suites use an LLM judge to assert pass/fail behaviors. Best for catching regressions in agent behavior without needing numeric baselines.

### 10.1 Create a Test Suite

```python
import opik

client = opik.Opik()

suite = client.get_or_create_test_suite(
    name="task-plan-regression",
    project_name="agent-runner",
    global_assertions=[
        "The output is valid YAML.",
        "The output contains a 'tasks' key at the top level.",
        "Every task has an 'id', 'title', 'description', and 'priority' field.",
        "No two tasks have the same 'id'.",
        "The 'priority' field for each task is one of: high, medium, or low.",
    ],
    global_execution_policy={
        "runs_per_item":   2,   # run each item twice (LLM is non-deterministic)
        "pass_threshold":  2,   # both runs must pass
    },
)
```

### 10.2 Insert Test Cases

```python
suite.insert([
    {
        "data": {
            "story": "Add login validation to the authentication form.",
            "acceptance_criteria": ["Form rejects empty fields", "Form rejects invalid email"],
        }
    },
    {
        "data": {
            "story": "Implement rate limiting on the /api/orders endpoint.",
            "acceptance_criteria": ["429 returned after 100 req/min", "Retry-After header present"],
        },
        # Optional item-level assertion override:
        "assertions": ["The output includes a task for implementing the rate-limit middleware."],
        "execution_policy": {"runs_per_item": 3, "pass_threshold": 2},
    },
])
```

### 10.3 Run the Suite

```python
def task(item: dict) -> dict:
    """Call the actual task-generator for this test item."""
    tasks_yaml = call_task_generator_agent(item["story"], item["acceptance_criteria"])
    return {"input": item, "output": tasks_yaml}


result = opik.run_tests(test_suite=suite, task=task)
print(f"Pass rate: {result.pass_rate:.0%}")

if result.pass_rate < 1.0:
    print("FAILED items:")
    for item in result.test_results:
        if not item.passed:
            print(f"  - {item.data}")
```

### 10.4 Recommended Test Suites per Stage

| Suite Name | Assertions |
|---|---|
| `intake-regression` | "story.yaml has change_id, title, acceptance_criteria", "constraints.md is non-empty" |
| `task-plan-regression` | "tasks list non-empty", "each task has required fields", "no duplicate IDs" |
| `assignment-regression` | "execution_schedule is a list of batches", "all task IDs appear in schedule", "no task appears in two batches" |
| `implementation-regression` | "impl_report.yaml has status field", "status is one of: complete, partial, failed" |
| `qa-regression` | "qa_report.yaml has overall_result", "each acceptance criterion has a verdict" |
| `lessons-regression` | "lessons_optimizer_report.yaml is non-empty", "contains at least one lesson entry" |

---

## 11. Gap-Filling: CLI Agent Tracing

### 11.1 The Gap

`run_agent_cmd()` in `run_cmds.py` invokes Claude Code CLI as a subprocess. Its execution is **invisible to Opik** — only stdout text is captured. The Prefect task wrapper provides no Opik context.

### 11.2 Wrapping CLI Calls with Tool Spans

Inside each `step_*` function in `steps.py`, nest a `tool` span around the `run_agent_cmd` call:

```python
# Before (steps.py:49–53)
with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
    trace.input = {"intake_source": intake_source, "change_id": change_id}
    result = run_agent_cmd(runner=runner, prompt=prompt, agent="intake-agent")
    trace.output = {"stdout_preview": result[:2000]}

# After: add a nested tool span
with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
    trace.input  = {"intake_source": intake_source, "change_id": change_id}
    trace.thread_id = change_id   # <-- enables Thread grouping
    
    with opik.start_as_current_span("run-intake-agent", type="tool") as span:
        span.input = {
            "agent":          "intake-agent",
            "runner":         runner,
            "prompt_preview": prompt[:500],     # truncate — full prompt may be large
        }
        result = run_agent_cmd(runner=runner, prompt=prompt, agent="intake-agent")
        span.output = {
            "stdout_bytes":   len(result),
            "stdout_preview": result[:2000],
        }
    
    trace.output = {"stdout_preview": result[:2000]}
```

### 11.3 What to Log (and What Not To)

**Do log:**
- Agent name (`"intake-agent"`)
- Runner type (`"claude"`)
- First 500 chars of prompt (enough for debugging)
- stdout byte count + first 2000 chars
- Exit success/failure flag (if available)
- Timing metadata

**Do NOT log:**
- Full prompts (may contain sensitive artifact contents)
- Full stdout (can be megabytes from software-engineer-hyperagent)
- Secrets or API keys

### 11.4 Checking Exit Status

`run_agent_cmd` currently returns stdout as a string. If it is modified to raise on failure, catch and log:

```python
try:
    result = run_agent_cmd(runner=runner, prompt=prompt, agent="intake-agent")
    span.output = {"stdout_preview": result[:2000], "success": True}
except Exception as e:
    span.output = {"error": str(e), "success": False}
    raise
```

---

## 12. Gap-Filling: Artifact Event Tracking

### 12.1 The Gap

50+ YAML/JSON artifacts are written to `agent-context/{change_id}/` during a run. None of these writes are recorded in Opik.

### 12.2 Logging Artifact Writes as Spans

Create a thin wrapper around `Path.write_text`:

```python
# In opik_integration.py or a new artifact_io.py module

import opik
from pathlib import Path


def write_artifact(path: str | Path, content: str, change_id: str | None = None) -> None:
    """Write an artifact file and record a tool span in Opik."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with opik.start_as_current_span("write-artifact", type="tool") as span:
        span.input  = {"path": str(p.relative_to(Path.cwd()) if p.is_absolute() else p)}
        p.write_text(content, encoding="utf-8")
        span.output = {"bytes_written": len(content.encode())}


def read_artifact(path: str | Path) -> str:
    """Read an artifact file and record a tool span in Opik."""
    p = Path(path)
    with opik.start_as_current_span("read-artifact", type="tool") as span:
        span.input = {"path": str(p)}
        content = p.read_text(encoding="utf-8")
        span.output = {"bytes_read": len(content.encode())}
    return content
```

### 12.3 Tracking `inject_file_contents` Reads

`opik_integration.py:65` reads files to inject into evaluator prompts. Enhance it:

```python
def inject_file_contents(context: str) -> str:
    pattern = r"agent-context/[\w\-]+/[\w\-./]+"
    paths = re.findall(pattern, context)
    sections: list[str] = []
    for rel_path in paths:
        full_path = RUNNER_ROOT / rel_path
        if full_path.is_file():
            with opik.start_as_current_span("read-context-file", type="tool") as span:
                span.input = {"path": rel_path}
                body = full_path.read_text(encoding="utf-8")
                span.output = {"bytes": len(body.encode())}
            sections.append(f"### {rel_path}\n```\n{body}\n```")
    return "\n\n".join(sections)
```

---

## 13. Thread ID Convention for change_id Grouping

### 13.1 Why This Matters

By default, each stage creates an **isolated trace** with no connection to other stages. The Opik dashboard's **Threads** view groups related traces into a single conversation — but only when they share the same `thread_id`.

Setting `thread_id = change_id` on every trace means all 9 stages for one workflow run are visible as a single conversation timeline.

### 13.2 Where to Set It

In every `step_*` function in `steps.py`, add `trace.thread_id = change_id`. Some steps don't currently receive `change_id` as an argument — those need it threaded through.

Example for `step_intake` (already has `change_id`):

```python
with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
    trace.input    = {"intake_source": intake_source, "change_id": change_id}
    trace.thread_id = change_id   # ADD THIS
    ...
```

For stages without `change_id` in their signature, parse it from the `context` string or pass it explicitly.

### 13.3 Thread Metadata

Also log `change_id` in trace metadata for easy filtering:

```python
trace.metadata = {
    "change_id":   change_id,
    "stage":       "intake",
    "intake_mode": intake_mode,
    "runner":      runner,
}
```

---

## 14. Eval-Optimizer Loop Integration

### 14.1 Current Behavior

`evaluator_optimizer_loops.py` runs up to 3 iterations. It checks `"PASS" in evaluator_out` to stop early (lines 25–27, 47–49). No iteration-level data is logged to Opik.

### 14.2 Enhanced `run_eval_optimizer_loop` with Opik Scores

```python
@flow(log_prints=True, timeout_seconds=1800)
def run_eval_optimizer_loop(
    producer_func, producer_input, evaluator_func, evaluator_prompt,
    iter_count: int = 3, runner: str = "claude",
    change_id: str = "",       # pass for thread grouping
    stage_name: str = "",      # e.g. "task-gen"
):
    producer_out, evaluator_out = "", ""

    for i in range(iter_count):
        if i == 0 or not evaluator_out:
            combined_input = producer_input
        else:
            combined_input = (
                f"{producer_input}\n\n"
                f"## Evaluator Issues to Fix (iteration {i}):\n{evaluator_out}\n\n"
                f"Revise your output artifact to address the issues above. Do not ask questions — act immediately."
            )

        producer_out  = producer_func(combined_input, runner=runner)
        evaluator_out = evaluator_func(evaluator_prompt, runner=runner)
        passed = "PASS" in evaluator_out

        # Log iteration outcome to the active trace (if any)
        opik.opik_context.update_current_trace(
            feedback_scores=[
                {"name": "pass",           "value": 1.0 if passed else 0.0},
                {"name": "iteration_used", "value": float(i + 1)},
            ],
            metadata={
                "stage":      stage_name,
                "iteration":  i + 1,
                "passed":     passed,
                "change_id":  change_id,
            },
        )

        if passed:
            print(f"Evaluator passed on iteration {i + 1} — stopping loop early.")
            break

    return producer_out, evaluator_out
```

### 14.3 Enhanced `run_uow_eval_loop` with Opik Scores

```python
@task(log_prints=True, name="run-uow-eval-loop", timeout_seconds=3600)
def run_uow_eval_loop(
    uow_id: str, change_id: str, repo: str,
    iter_count: int = 3, runner: str = "claude",
) -> tuple[str, str]:
    producer_out, evaluator_out = "", ""

    for i in range(iter_count):
        producer_out = steps.step_software_engineer(
            uow_id=uow_id, change_id=change_id, repo=repo,
            evaluator_feedback=evaluator_out if i > 0 else "",
            runner=runner,
        )
        evaluator_out = steps.step_software_engineer_evaluator(
            uow_id=uow_id, change_id=change_id, repo=repo, runner=runner,
        )
        passed = "PASS" in evaluator_out

        opik.opik_context.update_current_trace(
            feedback_scores=[
                {"name": "uow_pass",       "value": 1.0 if passed else 0.0},
                {"name": "iteration_used", "value": float(i + 1)},
            ],
            metadata={
                "uow_id":    uow_id,
                "change_id": change_id,
                "iteration": i + 1,
                "passed":    passed,
            },
        )

        if passed:
            print(f"[{uow_id}] Evaluator passed on iteration {i + 1} — stopping loop early.")
            break

    return producer_out, evaluator_out
```

---

## 15. Production Observability

### 15.1 Online Evaluation

In the Opik dashboard → **Projects** → **agent-runner** → **Online Evaluation**:

1. Add a rule targeting all traces in this project
2. Select `AgentTaskCompletionJudge` or a custom G-Eval metric
3. Set sampling rate: 100% during development, 20–30% in steady state (reduces LLM cost)
4. Set model: `claude-haiku-4-5-20251001`

This auto-scores every incoming trace without any code changes.

### 15.2 Alerts

Configure alerts in Opik dashboard → **Alerts**:

| Alert | Condition | Action |
|---|---|---|
| Low pass rate | `evaluator_pass < 0.6` for 3 consecutive traces | Email + Slack |
| High iteration count | `iteration_used >= 3.0` in any eval-optimizer loop | Email |
| Error rate | Trace has no `output` set within 600s | PagerDuty |

### 15.3 Project Organization by Environment

Use separate `OPIK_PROJECT_NAME` values per environment:

```bash
# Development
OPIK_PROJECT_NAME=agent-runner-dev

# CI
OPIK_PROJECT_NAME=agent-runner-ci

# Production
OPIK_PROJECT_NAME=agent-runner
```

---

## 16. Anti-Patterns

| Anti-Pattern | Why It's a Problem | Correct Approach |
|---|---|---|
| Logging full artifact YAML to trace input | Token cost + PII risk; traces become huge | Log file path + byte count only |
| Skipping `client.flush()` in scripts | Traces silently lost before process exits | Always call `client.flush()` at end of short-lived scripts |
| Same `experiment_name` for different configs | Overwrites prior results; impossible to compare | Version names: `"task-gen-v1"`, `"task-gen-v2"` |
| `@opik.track` on Prefect `@task` at top level | Double-wrapping confuses context propagation | Put Opik spans **inside** the Prefect task body |
| Checking `"PASS" in result` without logging a score | No queryable metric; no dashboard visibility | Always follow the check with `update_current_trace(feedback_scores=[...])` |
| Creating datasets for every stage upfront | Over-engineering; most stages won't fail | Start with the stage that fails most; add others incrementally |
| Using GPT-4o (default) as judge model | Cost; data leaves your VPC if using Anthropic | Override to `claude-haiku-4-5-20251001` via `model=` parameter |
| `OPIK_TRACK_DISABLE=true` left on in production | All traces silently dropped | Only set this in unit tests; never in production or integration |
| Creating new `Opik()` clients per call | Expensive connection pool churn | Instantiate once at module level, reuse across calls |
| Logging full stdout from software-engineer-hyperagent | Can be MB of tool use output | Truncate to `result[:2000]` as currently done |

---

## 17. Quick Reference Card

### Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...              # Required: SDK evaluator calls
OPIK_API_KEY=<key>                        # Required: Opik Cloud (omit for self-hosted)
OPIK_WORKSPACE=<workspace>               # Optional: Cloud workspace name
OPIK_URL_OVERRIDE=http://localhost:5173/api  # Optional: self-hosted URL
OPIK_PROJECT_NAME=agent-runner           # Optional: project name (already set in opik_integration.py)
OPIK_TRACK_DISABLE=true                  # Optional: disable all tracing (tests only)
```

### Key Imports

```python
import opik
from opik import Opik
from opik.evaluation import evaluate
from opik.evaluation.metrics import (
    # Heuristic
    Equals, Contains, RegexMatch, IsJson, ROUGE, BERTScore,
    # LLM-as-judge
    Hallucination, AnswerRelevance, Moderation, GEval, MeaningMatch,
    AgentTaskCompletionJudge, AgentToolCorrectnessJudge,
    QARelevanceJudge, SummarizationCoherenceJudge,
)
from opik.evaluation.metrics import base_metric, score_result
from opik.integrations.anthropic import track_anthropic
```

### Common One-Liners

```python
# Trace a stage (already in steps.py)
with opik.start_as_current_trace("stage-name", project_name="agent-runner") as trace:
    trace.input    = {"key": val}
    trace.thread_id = change_id
    # ... do work ...
    trace.output   = {"result": result[:2000]}

# Nested span for a sub-operation
with opik.start_as_current_span("operation-name", type="tool") as span:
    span.input = {...}
    # ... do operation ...
    span.output = {...}

# Log feedback score to active trace
opik.opik_context.update_current_trace(
    feedback_scores=[{"name": "pass", "value": 1.0}],
    metadata={"iteration": i}
)

# Decorate a Python function
@opik.track(name="my-fn", type="llm", project_name="agent-runner")
def my_fn(x: str) -> str: ...

# Score with a metric
score = AgentTaskCompletionJudge(model="claude-haiku-4-5-20251001").score(
    input=task_description, output=impl_report
)

# Dataset CRUD
client = opik.Opik()
ds = client.get_or_create_dataset("my-dataset")
ds.insert([{"input": {...}, "expected_output": {...}}])

# Run an experiment
from opik.evaluation import evaluate
evaluate(dataset=ds, task=task_fn, scoring_metrics=[...], experiment_name="v1")

# Flush (short-lived scripts)
client = opik.Opik()
# ... do work ...
client.flush()
```

### Span Type Cheatsheet

| Operation | `type=` |
|---|---|
| LLM API call | `"llm"` |
| CLI subprocess | `"tool"` |
| Artifact read/write | `"tool"` |
| File search / retrieval | `"tool"` |
| Safety/guardrail check | `"guardrail"` |
| Everything else | `"general"` (default) |

---

## 18. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Traces not appearing in dashboard | Wrong API key or URL | Run `opik healthcheck`; verify `OPIK_API_KEY` and `OPIK_URL_OVERRIDE` |
| 403 errors | Expired or wrong credentials | Run `opik configure` to reset |
| Traces lost in short-lived runs | Missing `client.flush()` | Add `client.flush()` at end of script |
| Traces go to wrong project | Environment variable not set | Verify `OPIK_PROJECT_NAME=agent-runner` is exported before `run.py` |
| Nested spans not appearing | `@track` used outside active trace | Ensure outer trace is active before inner spans fire |
| Prefect + Opik context lost across tasks | Prefect tasks run in threads/processes | Open a new Opik trace **inside** each Prefect `@task`, not outside |
| `opik_context.update_current_trace()` silently no-ops | No active trace in this thread | Wrap calling code in `opik.start_as_current_trace(...)` |
| SDK evaluator calls not appearing under trace | `@opik.track` creates a root trace if no parent | Call `call_evaluator_sdk()` inside an `opik.start_as_current_trace(...)` block |
| Judge model costs too high | Default is GPT-4o nano | Override: `Hallucination(model="claude-haiku-4-5-20251001")` |
| Evaluate() hangs | Large dataset + sync scoring | Set `nb_samples=10` for quick runs; evaluate uses threading |
| `OPIK_TRACK_DISABLE=true` set but traces still appear | Env var not exported before import | Export the variable before Python starts; it is read at import time |

---

## Key Links

| Resource | URL |
|---|---|
| Official docs | https://www.comet.com/docs/opik/ |
| Python SDK reference | https://www.comet.com/docs/opik/sdk/python |
| Metrics reference | https://www.comet.com/docs/opik/evaluation/metrics/overview |
| GitHub | https://github.com/comet-ml/opik |
| Cloud signup | https://www.comet.com/signup?from=llm |
| Full LLM-readable docs | https://www.comet.com/docs/opik/llms-full.txt |

---

*Primary codebase files: `opik_integration.py`, `steps.py`, `evaluator_optimizer_loops.py`*
