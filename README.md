# agent-workbench

**Agent Workbench is a local mission-control UI for AI software delivery.** It turns a story, work item, or synthetic fixture into a traceable multi-agent workflow you can launch, watch, evaluate, and inspect without losing the thread.

The pipeline runs six coordinated stages, with implementation enhanced by an **evaluator-optimizer loop** that iterates up to three times per unit of work:

```text
intake → task generation → assignment → implementation ⟳ QA → lessons optimization
                                              ↑__ evaluator feedback __|
```

During implementation each unit of work (UoW) is executed by a software-engineer agent, then immediately scored by an implementation-evaluator agent. If the evaluator returns a **PASS**, the loop exits early; otherwise the evaluator's structured feedback is injected into the next iteration's prompt, giving the producer a chance to self-correct before QA. A generic `run_eval_optimizer_loop` primitive makes the same pattern available to any producer/evaluator agent pair in the pipeline.

It can run fully local synthetic stories for offline iteration or live Azure DevOps work items for integrated delivery.

| Local Agent Workbench UI | Opik observability command center |
|---|---|
| ![Agent Workbench Runs view showing a live multi-stage workflow stream](docs/assets/agent-runner-runs.png) | ![Opik project insights dashboard for agent-workbench traces](docs/assets/opik-insights.png) |

## What this repository gives you

| Capability | What it means for you |
|---|---|
| **A browser UI for agent work** | Submit runs, choose Claude/Copilot/Gemini, watch live stage events, cancel jobs, and inspect run history from `http://127.0.0.1:8742`. |
| **Synthetic + ADO inputs** | Develop safely with local JSON fixtures, then point the same runner at live Azure DevOps work items when you are ready. |
| **Traceable workflow artifacts** | Every stage writes structured artifacts under `agent-context/<change-id>/`, so downstream agents and reviewers can inspect the chain of decisions. |
| **Opik tracing and insights** | Bootstrap can start a local self-hosted Opik stack, wire project metadata, and deep-link each selected run to traces filtered for that change ID. |
| **Evaluation harness** | `eval/synthesize.py` creates empirically calibrated easy/medium/hard eval stories, and `eval/run_eval.py` runs repeatable evaluations with regression checks. |
| **Hermetic recordings** | Browser-launched runs can record CLI subprocess I/O into local cassettes for later inspection. |

## Why Opik?

Agent Workbench uses [Opik by Comet](https://github.com/comet-ml/opik) as its observability and evaluation platform. A few things worth knowing:

- **Open source.** Opik is fully open source (Apache 2.0), available on [GitHub](https://github.com/comet-ml/opik). You can run it locally via bootstrap or point Agent Workbench at Comet's hosted offering.
- **Zero known vulnerabilities.** The [Veracode SCA package summary for Opik](https://sca.analysiscenter.veracode.com/vulnerability-database/libraries/opik/python/pypi/lid-7718138/summary) currently reports **0 known vulnerabilities** for the PyPI library. This is a point-in-time third-party database signal — re-check the link for the latest status.
- **Self-hostable.** Bootstrap clones and starts the local Opik Docker stack automatically, so traces and evaluation data stay on your machine.

> **Testing and evaluation details:** see [`eval/README.md`](eval/README.md).

---

## Start in 60 seconds

1. Clone this repository.
2. Make sure **Python 3.9+**, **git**, and **Docker Desktop** are installed. Docker Desktop must be running if you want the bundled local Opik stack.
3. Run the bootstrap wrapper for your OS.
4. Open `http://127.0.0.1:8742`.
5. Install and authenticate at least one AI backend CLI (`claude`, `copilot`, or `gemini`) before launching real agent runs.

### macOS

```bash
./bootstrap.sh
```

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

The bootstrap flow will:

- create or reuse `.venv/`
- install `requirements.txt`
- materialize agents from `agent-sources/`
- clone or update a local Opik checkout under `~/.agent-runner/opik`
- start the local self-hosted Opik Docker stack
- persist local Opik dashboard metadata into `~/.agent-runner/config.json`
- start the local API + GUI server at `http://127.0.0.1:8742`

### External prerequisites

Bootstrap assumes these tools are already installed on the machine:

| Dependency | Required for | Notes |
|---|---|---|
| Python 3.9+ | bootstrap, `run.py`, `server/main.py`, eval tools | Bootstrap creates `.venv/`, but it does **not** install Python itself. |
| `git` | bootstrap, normal repo workflows | Used for the repo itself and for cloning/updating the local Opik checkout. |
| Docker Desktop | bootstrap + local self-hosted Opik | Must be running before bootstrap starts the Opik stack. |
| PowerShell | Windows bootstrap | Used by `bootstrap.ps1` and Opik's upstream `opik.ps1`. |
| Bash-compatible shell | macOS bootstrap | Used by `bootstrap.sh` and Opik's upstream `opik.sh`. |

For actual workflow execution, you also need **at least one** supported AI backend CLI installed and authenticated:

| CLI | Used for |
|---|---|
| `claude` | `--runner claude` |
| `copilot` | `--runner copilot` |
| `gemini` | `--runner gemini` |

Bootstrap does **not** try to automate vendor install/login flows for those CLIs. If none is installed yet, bootstrap will still start the local services and warn that runs will fail until a backend is ready.

### Optional external dependencies

| Dependency | When you need it |
|---|---|
| Azure CLI (`az`) | Required only for live Azure DevOps work-item mode |
| `azure-devops` Azure CLI extension | Required only for live Azure DevOps work-item mode |

---

## Run your first workflow

### From the browser

Open `http://127.0.0.1:8742`, fill in the **Runs** form, and click **Submit run**. The terminal pane streams structured stage events in real time.

When a run is selected and Opik is configured, click **Open current run in Opik**. That link opens the Opik project with traces filtered to the selected run's change ID, so it is clear which trace set you are inspecting.

### Where to find the Opik links

| In Agent Workbench | What opens |
|---|---|
| **Runs** -> select any run -> **Open current run in Opik** | The Opik project trace view filtered to that run's `change_id` / `thread_id`. |
| **Evaluate** -> **Open Opik evaluation workspace** | The Opik project workspace for experiments, evaluation, online scoring, and trace drill-down. |

### From the CLI

Run with the bundled `TEST-AC-001` synthetic story:

```bash
python run.py --repo /absolute/path/to/target/repo
```

Use a custom synthetic story fixture:

```bash
python run.py --repo /absolute/path/to/target/repo --story-file /path/to/custom_story.json
```

Run against Azure DevOps:

```bash
python run.py --repo /absolute/path/to/target/repo --ado-url 'https://dev.azure.com/<org>/<project>/_workitems/edit/123456'
```

This mode also requires Azure CLI plus the `azure-devops` extension to already be installed and authenticated.

Watch a live Azure DevOps CI/CD run until it finishes:

```bash
python watch_ado_run.py 'https://dev.azure.com/<org>/<project>/_build/results?buildId=2131917'
```

The watcher polls Azure DevOps with `az pipelines build show`, prints live status updates, and raises a terminal alert with a bell plus a desktop notification when the run completes or fails.

Choose a runner:

```bash
python run.py --repo /path/to/repo --runner gemini   # gemini (default model: gemini-2.5-flash)
python run.py --repo /path/to/repo --runner claude   # default
python run.py --repo /path/to/repo --runner copilot
```

Pass extra context to the intake agent:

```bash
python run.py --repo /path/to/repo --ado-url 'https://dev.azure.com/...' \
  --extra-context "See PR https://dev.azure.com/.../pullrequest/456 for implementation examples."
```

The text is appended verbatim to the intake prompt. The intake agent incorporates it into `story.yaml` and `constraints.md`, so all downstream stages inherit it through those artifacts.

Run `python run.py --help` for all options.

---

## Create and run calibrated eval stories

Use this flow when you want to generate the repository-wide eval corpus for a **single dataset** and immediately run it against a target repo.

### Step 1 — Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### Step 2 — Create a dataset manifest

Copy this example into a new file such as `eval/datasets/my-service.yaml`, then replace the paths and domain description with your own:

```bash
mkdir -p eval/datasets
cat > eval/datasets/my-service.yaml <<'YAML'
dataset_id: my-service
display_name: my-service
source:
  type: code_repository
  path: /absolute/path/to/my-service/src
  include_extensions:
    - .py
    - .ts
  exclude_patterns:
    - /node_modules/
    - /dist/
    - /coverage/
sampling:
  strategy: stratified
  sample_size: 50
  seed: 8675309
  stratify_by: layer
domain_context: >
  This is the repository you want to evaluate. Synthetic stories should ask
  the agent to implement realistic changes that match this codebase.
metadata:
  owner: platform-eval
YAML
```

### Step 3 — Lock the dataset sample

```bash
python3 eval/init_dataset.py --dataset eval/datasets/my-service.yaml
```

This writes:

- `eval/datasets/my-service.lock`
- `eval/datasets/samples/my-service_sample.jsonl`

### Step 4 — Synthesize and calibrate the eval stories

```bash
python3 eval/synthesize.py \
  --dataset eval/datasets/my-service.yaml \
  --repo /absolute/path/to/my-service \
  --runner copilot \
  --model gpt-5-mini \
  --output eval/suites \
  --stories-output eval/stories
```

What this does:

1. Generates one repository-wide story each for **easy**, **medium**, and **hard**.
2. Keeps the story title and description fixed.
3. Iteratively rewrites only the ACs.
4. Runs a faster default calibration profile: **3 workflow trials** per candidate
   AC set, with cheaper single-iteration workflow loops enabled during
   calibration.
5. Accepts the AC set only when the measured pass rate lands in-band:
   - **Easy:** `>= 75%`
   - **Medium:** `50% - 74%`
   - **Hard:** `25% - 49%`
6. Stops after **5 iterations** by default and automatically resumes from
   compatible raw-story checkpoints in `eval/suites/raw/` when possible.

While this command runs, it now streams live synthesis and calibration output to
the terminal. If the selected runner exposes token-by-token response text, you
will see that too, but hidden model reasoning is not available unless the
runner CLI itself reveals it.

The command writes:

```text
eval/suites/easy/
eval/suites/medium/
eval/suites/hard/
eval/suites/raw/
eval/suites/synthesis_report.json
eval/stories/story_001_easy.json
eval/stories/story_002_medium.json
eval/stories/story_003_hard.json
```

### Step 5 — Run the generated suites

```bash
python3 eval/run_eval.py --suite eval/suites/easy   --repo /absolute/path/to/my-service --runner copilot --model gpt-5-mini --skip-opik
python3 eval/run_eval.py --suite eval/suites/medium --repo /absolute/path/to/my-service --runner copilot --model gpt-5-mini --skip-opik
python3 eval/run_eval.py --suite eval/suites/hard   --repo /absolute/path/to/my-service --runner copilot --model gpt-5-mini --skip-opik
```

### Step 6 — Re-synthesize only flagged stories later

If you already have a calibration report or hint file and only want to rework flagged stories:

```bash
python3 eval/synthesize.py \
  --dataset eval/datasets/my-service.yaml \
  --repo /absolute/path/to/my-service \
  --runner copilot \
  --model gpt-5-mini \
  --output eval/suites \
  --stories-output eval/stories \
  --ac-hints eval/suites/calibration_report.json
```

For the full evaluation reference, artifact details, and troubleshooting, see [`eval/README.md`](eval/README.md).

---

## Local API + GUI

A local-first FastAPI server + single-file GUI wraps the headless runner so you can submit and monitor jobs from a browser.

```bash
pip install -r requirements.txt
python server/main.py
# -> http://localhost:8742
```

If you also want the bundled local Opik stack configured automatically, prefer the OS bootstrap wrapper above instead of starting `server/main.py` directly.

Manual `server/main.py` startup only requires Python dependencies. Docker is only needed when you want the local self-hosted Opik stack as well.

You can override the bind address at startup:

```bash
python server/main.py --host 127.0.0.1 --port 8742
python server/main.py --reload   # dev hot reload
```

The GUI (served at `/`) provides 5 views:

- **Runs** — submit a job, watch live SSE event streams, cancel running jobs, and open the current run in Opik with trace filters already applied.
- **Agents** — lists materialized agents from `agent-sources/*/v*/`; click an agent to read its latest prompt alongside bundle metadata.
- **Corpus** — lists `eval/stories/*.json` with per-story pass-rate history.
- **Evaluate** — gives a compact local summary and a prominent bridge into the Opik evaluation workspace.
- **Settings** — edit `~/.agent-runner/config.json` (host, port, defaults, concurrency, Opik project metadata).

State lives under `~/.agent-runner/`:

```
~/.agent-runner/
├── config.json            # Edited via Settings view
├── jobs.db                # SQLite job store
├── cassettes/<id>.jsonl   # Hermetic-mode CLI recordings
├── memory/                # Reserved for agent session memory
└── opik/                  # Local self-hosted Opik checkout used by bootstrap
```

### Hermetic mode

Selecting **Hermetic** mode on submit records every CLI subprocess invocation (cmd, stdout, stderr, exit code, duration) into `~/.agent-runner/cassettes/{change_id}.jsonl` for later inspection. The CLI still talks to the real backend; only the I/O is captured. Replay is not yet implemented.

### CLI compatibility

The CLI (`python run.py ...`) is unchanged when used standalone. Event emission and cassette recording are gated by `AGENT_RUNNER_EVENT_LOG` / `AGENT_RUNNER_CASSETTE` env vars set by the server when it spawns the subprocess.

### Useful API endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness + version |
| `GET` | `/` | Serves the single-file GUI |
| `POST` | `/runs` | Submit a regular run with the same inputs exposed by `run.py` |
| `GET` | `/runs` | List recent regular jobs by default; pass `run_kind=evaluation` for evaluation jobs |
| `GET` | `/runs/{job_id}` | Job detail including status, aggregates, and child jobs |
| `GET` | `/runs/{job_id}/events` | Replay the full event log as a JSON array |
| `GET` | `/runs/{job_id}/stream` | SSE stream of live events; supports `Last-Event-ID` and `?after=<seq>` |
| `POST` | `/runs/{job_id}/cancel` | Cancel a queued/running job |
| `GET` | `/agents` | Agent catalog from `agent-sources/*/v*/` |
| `GET` | `/agents/{name}` | Latest prompt text and metadata for a selected agent |
| `GET` | `/corpus` | Eval stories with history-derived pass rates |
| `GET` | `/evaluate/summary` | Aggregated pass-rate and regression summary |
| `POST` | `/evaluate/runs` | Submit a run for a selected evaluation story |
| `GET` / `PUT` | `/settings` | Read/update `~/.agent-runner/config.json` |

Example submission:

```bash
curl -X POST http://127.0.0.1:8742/runs \
  -H 'content-type: application/json' \
  -d '{
    "repo": "/absolute/path/to/target/repo",
    "change_id": "TEST-AC-001",
    "story_file": "agent-context/test-fixtures/synthetic_story.json",
    "runner": "claude",
    "mode": "live",
    "skip_materialize": false
  }'
```

---

## Synthetic Mode vs. ADO Mode

| | Synthetic | ADO |
|---|---|---|
| Credentials needed | ❌ None | ✅ Azure CLI |
| Network required | ❌ No | ✅ Yes |
| Input source | Local JSON file | Live ADO work item |
| ADO operations | Skipped | Active |

**Synthetic mode** is selected automatically when you pass `--story-file` (or use the default fixture). **ADO mode** is selected when you pass `--ado-url`.

---

## Synthetic Fixture Format

All synthetic story fixtures must be valid JSON objects with these required fields:

| Field | Type | Notes |
|---|---|---|
| `change_id` | string | e.g. `TEST-AC-001`. Can instead be passed via `--change-id`. |
| `title` | string | One-line title |
| `description` | string | Multi-line narrative |
| `acceptance_criteria` | list or object | See below |

### Acceptance Criteria

Either a list of strings or a keyed object — both are normalized to `AC1`, `AC2`, ... during intake:

```json
{ "acceptance_criteria": ["First criterion", "Second criterion"] }
```
```json
{ "acceptance_criteria": { "AC1": "First criterion", "AC2": "Second criterion" } }
```

Optional fields: `examples`, `constraints`, `non_functional_requirements`, `raw_input_notes`, `ado_metadata`.

### Bundled Fixtures

| File | Change ID | Purpose |
|------|-----------|---------|
| `agent-context/test-fixtures/synthetic_story.json` | `TEST-AC-001` | Smoke-test — validates workflow stages |
| `agent-context/test-fixtures/synthetic_story_medium.json` | `TEST-MEDIUM-001` | Multi-task decomposition scenario |

---

## Artifact Layout

```
agent-context/<change-id>/
├── events.jsonl            # Structured job/stage/CLI events for API replay + SSE
├── intake/
│   ├── story.yaml        # Normalized story + acceptance criteria
│   ├── config.yaml       # Workflow config (includes project_type marker)
│   └── constraints.md    # Extracted constraints and open questions
├── planning/             # tasks.yaml, assignments.json
├── execution/            # impl_report.yaml per UoW
├── qa/                   # qa_report.yaml
└── logs/
```

---

## Workflow Stages

1. **Intake** — Normalizes fixture/ADO context into `intake/*` artifacts
2. **Task Generation** — Decomposes story into `tasks.yaml`
3. **Task Assignment** — Schedules units of work into `assignments.json`
4. **Implementation** — Executes each UoW, writes `impl_report.yaml`
5. **QA** — Validates outputs, writes `qa_report.yaml`
6. **Lessons Optimization** — Captures learnings and best practices

When runs are launched through the local API, each stage also emits structured `job.start`, `stage.start`, `stage.end`, `cli.invoke`, `cli.exit`, and `job.end` events into `agent-context/<change-id>/events.jsonl`. The Runs view replays that file and then switches to live SSE for in-progress jobs.

---

## Testing

```bash
# Existing command-runner tests
python3 -m pytest -q tests/test_run_cmds.py

# Local API + GUI smoke coverage
python3 -m pytest -q tests/test_server_routes.py tests/test_server_events.py

# Whole repository test suite
python3 -m pytest -q tests/
```

The new server tests cover:

- GUI serving from `/`
- `/health`, `/runs`, `/settings`, `/agents`, `/corpus`, `/evaluate/summary`
- partial settings updates and validation failures
- JSONL event emission sequencing
- env-gated cassette recording

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Synthetic story fixture not found` | Bad path | Use an absolute path or `~` expansion |
| `must be a JSON object` | Array at top level or invalid JSON | Wrap in `{}`, validate syntax |
| `missing required field(s)` | `change_id`/`title`/`description`/`acceptance_criteria` absent or empty | Add the missing field |
| `acceptance_criteria must be a non-empty list...` | Empty, `null`, or non-string values | Use a non-empty list or map of strings |
| `change_id does not match` | `--change-id` and fixture `change_id` conflict | Remove one, or make them match |
| `Provide either ado_url or story_file, not both` | Both flags passed | Pick one mode |
| `api.port must be an integer between 1 and 65535` | Invalid Settings value or bad `--port` override | Choose a valid TCP port |
| Browser shows `API offline` | `server/main.py` is not running or host/port changed | Start the server and open the configured host/port |

---

## Planned Work

| Item | Description |
|------|-------------|
| **Meta-evaluation pipeline** | A second-order evaluation loop that assesses the quality of the evaluator agents themselves — checking whether their PASS/FAIL verdicts correlate with actual downstream outcomes. Enables automated tuning of evaluator prompts and scoring rubrics without manual review. |
| Cassette replay | Execute a full workflow run from a previously recorded cassette without hitting live AI backends. |
| Streaming token metrics | Real-time token usage and cost display in the metrics bar as each stage completes. |

---

## Synthetic Mode Markers

After intake, synthetic runs are identifiable by:

- `config.yaml` → `project_type: 'synthetic-fixture'`
- `story.yaml` → `raw_input.source_type: synthetic_fixture`
- `story.yaml` → **no** `ado_provenance` key
