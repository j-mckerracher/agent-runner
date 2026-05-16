# Difficulty-tier evaluation framework

The `eval/` package is the evaluation framework for Agent Workbench. It is split
into two phases:

1. **Setup** (run once, outputs committed to the repo): define a dataset
   manifest → lock a deterministic sample → synthesize three repository-wide
   eval stories with **predicted** easy/medium/hard tiers.
2. **Steady state** (run repeatedly, fast): run an existing suite against a
   target repository, execute deterministic checks, compute weighted scores, and
   compare against committed baselines.

```
SETUP (run once, artifacts committed to repo)
─────────────────────────────────────────────
Dataset Manifest → init_dataset.py → sample.jsonl + dataset.lock
                → synthesize.py   → eval/suites/{easy,medium,hard}/

STEADY STATE (run repeatedly)
──────────────────────────────
run_eval.py --suite eval/suites/hard/ --repo /path/to/repo
  → weighted composite score + per-tier scores
  → regression delta vs. committed baseline
  → optional Opik metrics
```

---

## Prerequisites

```bash
python3 -m pip install -r requirements.txt
```

Dataset manifests and suite manifests are YAML. Workflow-compatible story companions
under `eval/stories/` are JSON.

---

## Setting up a new dataset

There are two supported dataset source types for evaluation:

- **`code_repository`** — point at any local code repository; the framework
  walks it, catalogues source files by architectural layer, and builds the
  sample automatically. **This is the recommended type for coding agent evals.**
- **Tabular sources** (`csv`, `jsonl`, `sqlite`, `parquet`, `postgres`,
  `s3_glob`) — for structured data rows.

### Step 1 — Write a dataset manifest

Create `eval/datasets/<your-dataset>.yaml`. Choose either format:

**Code repository** (for evaluating a coding agent against a real codebase):

```yaml
dataset_id: my-service
display_name: my-service
source:
  type: code_repository
  path: ~/Code/my-service/src        # supports ~ expansion; relative paths
                                     # are resolved from this manifest file
  include_extensions:                # optional — defaults to common code exts
    - .cs
    - .csproj
  exclude_patterns:                  # optional — defaults to /obj/, /bin/, etc.
    - /obj/
    - /bin/
    - .AssemblyInfo.cs
  layer_map:                         # optional — override auto-inferred layers
    MyService.CoreLib: core
sampling:
  strategy: stratified               # random | stratified | head | manual
  sample_size: 50
  seed: 8675309
  stratify_by: layer                 # required for stratified; auto-populated
                                     # by the code_repository reader
domain_context: >
  This is a C# payments API. Synthetic stories should ask the agent to
  implement features such as adding endpoints, updating validation logic,
  and writing tests that span the API, service, and data layers.
metadata:
  owner: platform-eval
```

**Tabular (CSV / JSONL)**:

```yaml
dataset_id: claims_denials
display_name: Claims denial examples
source:
  type: csv                          # csv | jsonl | sqlite | parquet | postgres | s3_glob
  path: data/claims_denials.csv
sampling:
  strategy: random                   # no stratify_by needed for random sampling
  sample_size: 50
  seed: 8675309
domain_context: >
  Synthetic stories should ask the agent to implement features that help
  engineers inspect claims denial records.
metadata:
  owner: platform-eval
```

> **Manifest required fields:** `dataset_id`, `display_name`, `source.type`,
> source-specific path/connection settings, `sampling.strategy`,
> `sampling.sample_size`, `sampling.seed`, `domain_context`.
>
> **Stratified sampling** additionally requires `sampling.stratify_by` — for
> `code_repository` sources this should be `layer`, which is populated
> automatically.

---

### Step 2 — Initialize the dataset

```bash
python3 eval/init_dataset.py --dataset eval/datasets/my-service.yaml
```

This command:

1. Reads the source (walks the repo, or reads the CSV/JSONL/etc.)
2. Applies the configured sampling strategy
3. Writes `eval/datasets/samples/my-service_sample.jsonl` — the locked sample
4. Writes `eval/datasets/my-service.lock` — the schema fingerprint

**Expected output:**

```
Initialized dataset sample: 50 records
Fields: file, filename, layer, project, size_bytes, sub_path
Source type: code_repository
Sampling strategy: stratified
Stratum distribution: {"api": 4, "api_model": 5, "business_logic": 9, ...}
Sample path: eval/datasets/samples/my-service_sample.jsonl
Lock path: eval/datasets/my-service.lock
```

**Commit both output files.** They are the stable reference point for all
downstream synthesis and eval runs.

---

### Step 3 — Synthesize eval suites

```bash
python3 eval/synthesize.py \
  --dataset eval/datasets/my-service.yaml \
  --runner copilot \
  --model gpt-5-mini \
  --output eval/suites/ \
  --stories-output eval/stories/
```

By default this runs three internal stages:

1. **Repository-wide story synthesis** — the agent reads the locked sample plus
    repository metadata and generates exactly three repository-wide stories:
    one **easy**, one **medium**, and one **hard**.
2. **Deterministic tier analysis** — the framework attaches heuristic metadata
    explaining the predicted tier (`metadata.tiering`) for each story. This is
    cheap and repeatable, but it is **not** a proof of difficulty.
3. **Materialization** — the predicted-tier stories are written to their tiered
    suite outputs and workflow-compatible fixture companions.

The default path is designed for bring-your-own-repo onboarding: it avoids the
quota-heavy workflow fan-out that full calibration requires.

### Optional advanced mode — empirical calibration

If you want to spend the extra time and model budget to calibrate the generated
stories empirically, opt in explicitly:

```bash
python3 eval/synthesize.py \
  --dataset eval/datasets/my-service.yaml \
  --repo /absolute/path/to/my-service \
  --runner copilot \
  --model gpt-5-mini \
  --output eval/suites/ \
  --stories-output eval/stories/ \
  --calibrate
```

In calibration mode, each story keeps its `title`, `description`, and `prompt`
fixed while the agent rewrites **only the acceptance criteria** until the
measured difficulty lands in the requested band. This mode is intentionally
expensive and should be reserved for advanced authoring workflows rather than
the default dataset setup flow.

`eval/synthesize.py` streams live runner output during story generation and,
when `--calibrate` is enabled, during calibration workflow runs too.

**Useful options:**

| Flag | Default | Purpose |
|------|---------|---------|
| `--repo` | inferred when possible | Target repository root used only when `--calibrate` is enabled |
| `--runner` | `copilot` | LLM runner: `claude`, `copilot`, `gemini`, or a configured runner alias |
| `--model` | runner default | Optional model override. With the default `copilot` runner, the resolved default model is `gpt-5-mini`. |
| `--stories-output` | `eval/stories/` | Workflow-compatible JSON output directory |
| `--calibrate` | `false` | Opt in to empirical workflow-run calibration |
| `--calibration-runner-profile` | `copilot-gemma4=2,copilot-deepseek-v4-flash=2,copilot-minimax-m2.7=2` | Mixed runner/count profile used for calibration workflow trials |
| `--calibration-runs` | `3` | Legacy single-runner trial count used only when you opt out of the mixed runner profile |
| `--calibration-max-iterations` | `5` | Maximum AC rewrite attempts per story |
| `--batch-size` | `5` | Legacy compatibility flag; repository-wide synthesis ignores batching |
| `--calibration-story-workers` | `2` | Maximum stories to calibrate at once |
| `--calibration-max-concurrent` | `2` | Legacy workflow-run budget hint; mixed profiles still ensure all configured runs launch for each difficulty tier |
| `--[no-]calibration-fast-mode` | `true` | Toggle the cheaper one-iteration workflow profile used for calibration |
| `--ac-hints` | — | Hint JSON; re-synthesizes only flagged stories |

**Expected outputs:**

```
eval/suites/
├── easy/
│   ├── suite_manifest.yaml
│   └── story_001_easy.yaml
├── medium/
│   ├── suite_manifest.yaml
│   └── story_002_medium.yaml
├── hard/
│   ├── suite_manifest.yaml
│   └── story_003_hard.yaml
├── raw/                         # intermediate artifacts — safe to gitignore
└── synthesis_report.json
eval/stories/
├── story_001_easy.json
├── story_002_medium.json
└── story_003_hard.json          # workflow-compatible JSON companions
```

**Commit everything under `eval/suites/{easy,medium,hard}/` and
`eval/stories/`.** The `raw/` directory is not needed for eval runs.

---

### Step 4 — Run evals (steady state)

```bash
# Run the hard suite against a repo
python3 eval/run_eval.py \
  --suite eval/suites/hard/ \
  --repo /path/to/repo \
  --skip-opik

# Run a single story
python3 eval/run_eval.py \
  --story eval/suites/hard/story_003_hard.yaml \
  --repo /path/to/repo \
  --skip-opik
```

The first successful run writes a baseline at `eval/.baselines/hard.json`.
Subsequent runs compute the regression delta against that baseline.

**Useful options:**

| Flag | Purpose |
|------|---------|
| `--runner claude\|copilot\|gemini` | Select the workflow runner (runner aliases are also supported) |
| `--model` | Model name |
| `--runs N` | Repeat each story N times |
| `--max-concurrent N` | Parallelise up to N story/run jobs |
| `--skip-pipeline` | Skip `run.py`; score existing `agent-context/` artifacts |
| `--mono-root` | Alias for `--repo` |
| `--change-id` | Locate a story by ID without `--suite`/`--story` |
| `--skip-opik` | Disable Opik metric reporting |
| `--regression-threshold 0.05` | Exit non-zero if composite score drops > 5% |
| `--update-baseline` | Intentionally replace the committed baseline |
| `--ci` | CI-friendly mode; implies non-zero exit on regression |

`--story` accepts either a suite YAML file or a workflow-compatible JSON fixture.
`--change-id` can resolve either `eval/stories/<id>.json` or a matching suite YAML.

---

## Source types reference

| Type | Requires | Notes |
|------|----------|-------|
| `code_repository` | `source.path` (repo root, `~` supported) | Walks the repo, infers `layer` from directory names. Supports `include_extensions`, `exclude_patterns`, `layer_map`. |
| `csv` | `source.path` | Standard CSV with header row. |
| `jsonl` | `source.path` | One JSON object per line. |
| `sqlite` | `source.path` + `source.table` or `source.query` | Exactly one of table/query. |
| `parquet` | `source.path` + `pyarrow` or `pandas` installed | Fails with actionable error if dependencies are missing. |
| `postgres` | `source.connection_string` (or `dsn`/`url`) + `source.query` | Requires `psycopg`. |
| `s3_glob` | `source.uri` or `source.pattern` | Requires `boto3`/`s3fs`. |

### `code_repository` layer inference

When no `layer_map` is provided, the reader automatically infers a layer from
each top-level directory name using these rules (checked in order):

| Directory name contains | → layer |
|-------------------------|---------|
| `test` + `integration` | `tests_integration` |
| `test` | `tests` |
| `api` + `model` | `api_model` |
| `api` or `controller` | `api` |
| `model` or `entity` or `dto` | `model` |
| `dal` or `data` or `repository` | `data_access` |
| `bll` or `service` or `business` | `business_logic` |
| `ui` or `web` or `frontend` | `presentation` |
| anything else | sanitized directory name |

Override any mapping with `source.layer_map` in the manifest.

---

## Sampling strategies

| Strategy | Extra required field | When to use |
|----------|----------------------|-------------|
| `stratified` | `stratify_by: <field>` | Proportional coverage across categories; recommended for code repos with distinct layers |
| `random` | — | Unbiased draw; use when no meaningful strata exist |
| `head` | — | First N records; use for ordered datasets (e.g. most recent events) |
| `manual` | `indexes` or `ids` | Curated golden sets or regression fixtures |

---

## Baselines and CI

Baselines live under `eval/.baselines/`:

- **First successful run** writes the initial baseline automatically.
- `--update-baseline` intentionally replaces the existing baseline.
- `--regression-threshold` (default `0.0`) controls the tolerated composite-score drop.
- Runs exit non-zero when the threshold is breached; `--ci` is accepted for CI compatibility.

**Commit baselines** when they represent the accepted quality bar for a suite.

---

## Opik reporting

Use `--skip-opik` for local runs. When Opik is enabled:

- Individual check metrics are named `{check_id}_{check_subject}` (no difficulty suffix).
- Tags carry `difficulty:high/medium/low`, `mechanism:command/matches/contains`,
  `failure:BUILD_ERROR/ASSERTION_MISS/TIMEOUT/NO_ATTEMPT`, and `suite_tier:hard/medium/easy`.
- The experiment name is `{suite_tier}_suite_{ISO_timestamp}`.

---

## Plugin authoring

Plugins add project-specific checks without modifying core framework code. See
[`PLUGIN_AUTHORING.md`](PLUGIN_AUTHORING.md) for the full protocol and
examples. The current API version is in [`PLUGIN_API_CHANGELOG.md`](PLUGIN_API_CHANGELOG.md).

---

## Advanced calibration validation

Use this validator when you explicitly work in the advanced calibration path and
want to audit a committed suite against a repo to produce a report with rewrite
hints:

```bash
python3 eval/validate_calibration.py \
  --suite eval/suites/ \
  --runs 5 \
  --output eval/suites/calibration_report.json
```

The report classifies the suite as `well_calibrated`, `overall_too_hard`,
`overall_too_easy`, or `medium_miscalibrated`, and provides narrative rewrite
suggestions for flagged ACs. Classification is pure Python; the LLM is called
once only to generate human-readable suggestions.

To re-synthesize only flagged stories using the hints:

```bash
python3 eval/synthesize.py \
  --dataset eval/datasets/my-service.yaml \
  --output eval/suites/ \
  --repo /absolute/path/to/my-service \
  --calibrate \
  --ac-hints eval/suites/calibration_report.json
```

---

## Committed artifacts

| Artifact | Commit? |
|----------|---------|
| `eval/datasets/*.yaml` (manifests) | ✅ always |
| `eval/datasets/*.lock` (schema fingerprints) | ✅ always |
| `eval/datasets/samples/*.jsonl` (locked samples) | ✅ always |
| `eval/suites/{easy,medium,hard}/` (tiered stories) | ✅ always |
| `eval/stories/*.json` (workflow companions) | ✅ always |
| `eval/.baselines/*.json` (accepted baselines) | ✅ when accepted |
| `eval/plugins/*.py` (project-specific checks) | ✅ always |
| `eval/suites/raw/` (synthesis intermediates) | ❌ gitignore |
| Run logs, exploratory reports | ❌ optional |

---

## Troubleshooting

- **`code_repository` produced no records** — check `include_extensions` matches the
  file types in the repo (e.g. `.cs` not `cs`), and that `exclude_patterns` isn't
  too broad. Run `python3 eval/init_dataset.py` to see the count and layer distribution.
- **Missing dataset** — check the path passed to `--dataset` and ensure the file
  is committed.
- **Schema drift** — re-run `init_dataset.py` and review lock-file changes before
  updating suites.
- **Calibration workflow failure** — only relevant when `--calibrate` is
  enabled. `eval/synthesize.py` includes the failing run exit codes plus a
  compact excerpt of workflow output in the error message, and it preserves the
  matching `agent-context/calibration_*` artifacts so you can inspect the failed
  attempt directly.
- **Plugin API mismatch** — update the plugin `api_version` or adapt it to the
  current protocol in `PLUGIN_API_CHANGELOG.md`.
- **Command check timeout** — raise the check timeout only after confirming the
  command is deterministic and scoped to the target repository.
- **Unsupported source connector** — install the documented optional dependency
  or switch to a supported connector.
- **Missing Opik credentials** — use `--skip-opik` for local deterministic runs.
- **Regression exits** — inspect the composite score, per-tier scores, failed
  check metadata, and baseline before using `--update-baseline`.

---

## CI guidance

CI should: install requirements, run `init_dataset.py` if the lock is stale,
run a representative suite with `--skip-opik` or configured Opik credentials,
compare against committed baselines with `--regression-threshold`, and upload
run artifacts. Keep live LLM calls, credentialed Opik, and external dataset
access out of unit tests — use deterministic fixtures instead.
