# Gemini Context: Evaluation Framework (eval/)

This directory contains the evaluation framework for the `agent-runner` project. It is designed to test and score AI agents' ability to perform specific tasks (stories) against a target Angular monorepo (`mcs-products-mono-ui`).

## Project Overview

The evaluation framework automates the lifecycle of testing an agent:
1.  **Setup:** Stashes changes and switches the target monorepo to a frozen testing branch.
2.  **Execution:** Runs the agent against a specific "story" (task).
3.  **Scoring:** Executes story-specific verification checks (e.g., file contents, regex matches, build/test commands).
4.  **Logging:** Records results and metrics to Opik for experiment tracking.
5.  **Cleanup:** Restores the target monorepo to its original state.

### Key Components

-   `run_eval.py`: The primary entry point for running evaluations. Supports single runs, concurrent batch runs, and skipping pipeline/logging.
-   `story_checks.py`: Contains the logic for verifying story completion. It defines evaluators like `_contains`, `_matches`, and `_command`.
-   `metrics.py`: Custom Opik metrics that wrap `story_checks.py` results.
-   `checks.sh`: A bash wrapper for `story_checks.py`.
-   `stories/`: JSON files defining evaluation stories, including descriptions, acceptance criteria, and constraints.

## Building and Running

### Prerequisites
-   Python 3.10+
-   Access to the `mcs-products-mono-ui` repository.
-   Dependencies installed from `requirements.txt` (in the project root).

### Running an Evaluation
To run an evaluation for a specific story:
```bash
python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mcs-products-mono-ui
```

### Common Flags
-   `--runs N`: Run the evaluation N times.
-   `--max-concurrent N`: Maximum number of concurrent runs.
-   `--skip-opik`: Disable logging to Opik (useful for local development).
-   `--skip-pipeline`: Score the current state of the repo without running the agent again.

### Scoring Directly
To score the current state of the monorepo without using the full runner:
```bash
bash eval/checks.sh /path/to/mcs-products-mono-ui EVAL-001
```

## Development Conventions

### Adding a New Story
1.  Create a new JSON file in `eval/stories/` (e.g., `EVAL-003.json`).
2.  Follow the structure of existing stories (see `EVAL-001.json`).
3.  Implement corresponding verification logic in `eval/story_checks.py` if custom checks are required.

### Testing Patterns
The framework expects agents to follow specific architectural and testing patterns within the target monorepo:
-   **Locators:** Defined in `*.locators.ts` files using `selectByDataTestId()`.
-   **Test Harnesses:** Cypress tests should use established test harness patterns (`*.component.test-harness.ts`).
-   **State Management:** Reactive updates using Angular signals (e.g., `computed`).
-   **Components:** PrimeNG components are preferred where specified.

## Integration with Opik
The framework uses Opik for experiment tracking. Ensure you have the necessary environment variables configured if not using `--skip-opik`.
