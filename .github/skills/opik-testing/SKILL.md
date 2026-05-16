---
name: opik-testing
version: 1.0.0
description: >
  Opik v1 pytest integration guide for regression testing and unit testing LLM applications.
  Use this skill when writing, debugging, or reviewing LLM unit tests with pytest, tracing test execution,
  capturing evaluation scores, and integrating with the Opik testing framework.
  Keywords: Opik, pytest, llm_unit, regression testing, LLM unit tests, @pytest.mark.opik, test_id,
  @track decorator, llm_unit fixture, parametrize, expected_output_key, pytest hooks, experiments dataset.
---

## When to Use This Skill

Activate this skill when:

- Writing LLM unit tests with `@llm_unit()` decorator
- Integrating Opik pytest plugin with your test suite
- Tracing LLM applications in tests using `@track` decorator
- Setting up pytest configuration for Opik integration (`--opik` flag, `opik_pytest_enabled`)
- Parametrizing tests with `@pytest.mark.parametrize` for multiple LLM test cases
- Capturing test results and scores in Opik experiments
- Debugging test pass/fail rates and individual test results
- Building regression testing for LLM applications before production deployment

---

## Key Concepts

**llm_unit Decorator**: Wraps test functions to enable Opik tracking. Automatically activates Opik pytest hooks when tests are collected. Can accept `expected_output_key` parameter for assertion-based validation.

**@track Decorator**: Traces your LLM application code. Works in conjunction with `@llm_unit()` to provide detailed execution traces within test context.

**Experiments Dataset**: Each test run creates a new experiment in Opik's `tests` dataset. Individual test results are logged and aggregated for pass/fail rate reporting.

**Pytest Hooks**: Activate automatically when `llm_unit` tests are collected. Force enablement with `--opik` CLI flag or `opik_pytest_enabled = true` in pytest config if using non-llm_unit tests.

**Parametrize Integration**: Use `@pytest.mark.parametrize` with `@llm_unit()` to run the same test with multiple input/output pairs efficiently.

---

## Setup

### Installation

Opik pytest integration is included with the Opik package:

```bash
pip install opik
```

### Configuration

Enable Opik pytest plugin in `pytest.ini` or `pyproject.toml`:

```ini
[pytest]
opik_pytest_enabled = true
```

Or force enable at runtime:

```bash
pytest --opik
```

---

## Writing Tests

### Basic LLM Unit Test

```python
import pytest
from opik import track, llm_unit

@track
def llm_application(user_question: str) -> str:
    # Your LLM application logic
    return "Paris"

@llm_unit()
def test_simple_passing_test():
    user_question = "What is the capital of France?"
    response = llm_application(user_question)
    assert response == "Paris"
```

### Parametrized Tests with Expected Output

```python
import pytest
from opik import track, llm_unit

@track
def llm_application(user_question: str) -> str:
    return "Paris"

@llm_unit(expected_output_key="expected_output")
@pytest.mark.parametrize("user_question, expected_output", [
    ("What is the capital of France?", "Paris"),
    ("What is the capital of Germany?", "Berlin")
])
def test_multiple_cases(user_question, expected_output):
    response = llm_application(user_question)
    assert response == expected_output
```

### Running Tests

```bash
# Auto-enable Opik for llm_unit tests
pytest tests/

# Force Opik enabled (useful without llm_unit tests)
pytest --opik tests/

# View experiments in Opik platform
# Navigate to the `tests` dataset to see results by test run
```

---

## Testing Best Practices

1. **Use pytest for fast regression checks on known behaviors.** `@llm_unit()` is best when you already know the expected output shape or pass/fail assertion you want to enforce in code.
2. **Promote real bugs into tests.** Follow Opik's evaluation guidance: when production traces reveal a failure, turn that example into a regression case instead of relying on ad hoc re-checking.
3. **Keep assertions narrow and deterministic.** Use exact or strongly bounded assertions for unit-style tests, and reserve broader quality judgments for Opik evaluation metrics.
4. **Use `@track` inside tests that matter.** A failing test is much more actionable when the trace shows the retrieval step, tool call, or prompt that caused it.
5. **Reach for `evaluate()` or `evaluate_threads()` when pytest becomes too narrow.**
   - Use `evaluate()` for dataset-based scoring across many examples.
   - Use `evaluate_threads()` for multi-turn conversation quality, coherence, and user frustration analysis.
6. **Test safety-critical behavior with dedicated metrics.** For moderation, compliance, or similar policies, combine pytest regressions with Opik metrics such as `Moderation` instead of encoding every rule manually in assertions.
7. **Separate unit checks from benchmark runs.** Use pytest in local development and CI for fast failures; use Opik experiments for slower comparative runs, score breakdowns, and trend tracking.

### When to switch from pytest to evaluation APIs

| Use case | Better tool |
| --- | --- |
| Deterministic assertion on one function or prompt | `pytest` + `@llm_unit()` |
| Compare prompt/model versions on a dataset | `opik.evaluate()` |
| Score full conversations across turns | `evaluate_threads()` |
| Measure safety or moderation quality | `Moderation` metric + experiments |

---

## Reference

- **Opik Testing Docs**: https://www.comet.com/docs/opik/v1/testing/pytest_integration
- **Evaluation Overview**: https://www.comet.com/docs/opik/evaluation/overview
- **Evaluate Threads**: https://www.comet.com/docs/opik/evaluation/evaluate_threads
- **Moderation Metric Guide**: https://www.comet.com/docs/opik/evaluation/evaluate_moderation_metric
- **Moderation Metric Reference**: https://www.comet.com/docs/opik/evaluation/metrics/moderation
- **Track Decorator**: Traces execution of LLM application functions within test context
- **Expected Output Key**: Parameter name in `@llm_unit(expected_output_key="...")` that pytest uses for automatic assertion
- **Pytest Hooks**: Auto-enabled or explicitly set via `--opik` / `opik_pytest_enabled`
- **Tip**: For detailed evaluation reports during development, consider using Opik's `evaluate()` function instead of pytest for broader assessment scenarios
