# Difficulty-tier evaluation framework

The `eval/` package is the evaluation framework for Agent Workbench. It is split
into two workflows:

1. **Setup time**: define a dataset manifest, lock a deterministic sample,
   synthesize stories and acceptance criteria, then bucket them into easy,
   medium, and hard suites.
2. **Steady state**: run an existing suite or story against a target repository,
   execute deterministic checks, compute weighted scores, compare baselines, and
   optionally report metrics to Opik.

The framework provides stable scaffolding, typed models, YAML IO, plugin
contracts, check helper primitives, documentation, dataset initialization, suite
synthesis, and the steady-state suite runner. Calibration remains advisory and
is implemented separately.

## Setup

Install repository dependencies before using the framework:

```bash
python3 -m pip install -r requirements.txt
```

The framework expects YAML support to be available because dataset manifests,
suite manifests, and story manifests are YAML.

## Dataset manifest

Author dataset manifests under `eval/datasets/<dataset>.yaml`. A minimal
manifest looks like:

```yaml
dataset_id: claims_denials
display_name: Claims denial examples
source:
  type: csv
  path: data/claims_denials.csv
sampling:
  strategy: random
  sample_size: 50
  seed: 8675309
domain_context: >
  Synthetic stories should ask the agent to implement features that help
  engineers inspect claims denial records.
metadata:
  owner: platform-eval
```

Required fields are `dataset_id`, `display_name`, `source.type`,
source-specific settings, `sampling.strategy`, `sampling.sample_size`,
`sampling.seed`, and `domain_context`. CSV and JSONL sources require
`source.path`. SQLite sources require `source.path` plus exactly one of
`source.table` or `source.query`. Parquet, Postgres, and S3 glob manifests are
validated, but their source readers fail with actionable optional-dependency
messages unless you install the needed connector packages.

## Initialize a dataset

The setup command locks a deterministic sample and schema fingerprint:

```bash
python3 eval/init_dataset.py --dataset eval/datasets/claims_denials.yaml
```

Expected outputs:

- `eval/datasets/samples/claims_denials_sample.jsonl`
- `eval/datasets/claims_denials.lock`

Unsupported source connectors should fail with actionable errors rather than
silently falling back to another source.

## Synthesize suites

After a dataset is initialized, synthesize stories and acceptance criteria:

```bash
python3 eval/synthesize.py --dataset eval/datasets/claims_denials.yaml --output eval/suites/
```

Useful options:

- `--runner`, `--model`, and `--agent` select the synthesis-time agent runner.
- `--batch-size` controls how many locked sample records are sent per synthesis
  call.
- `--ac-hints eval/suites/calibration_report.json` passes non-destructive
  calibration hints for flagged stories; existing unflagged raw stories are
  reused when present.

Expected outputs:

- `eval/suites/easy/suite_manifest.yaml`
- `eval/suites/medium/suite_manifest.yaml`
- `eval/suites/hard/suite_manifest.yaml`
- tiered story YAML files under each suite directory
- workflow-compatible JSON companions under `eval/stories/` for existing server
  corpus compatibility
- raw synthesis artifacts under `eval/suites/raw/`
- `eval/suites/synthesis_report.json`

## Run an evaluation suite

Run the hard suite against a repository:

```bash
python3 eval/run_eval.py --suite eval/suites/hard/ --repo /path/to/repo --skip-opik
```

Run a single story manifest:

```bash
python3 eval/run_eval.py --story eval/suites/hard/story_001_hard.yaml --repo /path/to/repo --skip-opik
```

The runner will use the existing Agent Workbench workflow path (`run.py`) rather
than creating a separate implementation pipeline.

Useful runner options:

- `--runner claude|copilot|gemini` and `--model` select the workflow runner.
- `--runs` repeats each story; `--max-concurrent` limits how many story/run
  jobs execute at once while preserving deterministic result ordering.
- `--skip-pipeline` skips `run.py` and evaluates the best available
  `agent-context/<change_id>/` artifact text.
- `--skip-materialize` is forwarded to `run.py` when the pipeline is launched.
- `--mono-root` is a safe alias for `--repo`.
- `--change-id <id>` can locate `eval/stories/<id>.json` or a matching suite
  story YAML when `--story`/`--suite` is omitted.

## Baselines and CI

The runner maintains baselines under `eval/.baselines/`:

- First successful run writes an initial baseline.
- `--update-baseline` intentionally replaces an existing baseline.
- `--regression-threshold` controls the tolerated composite-score drop.
- Runs exit non-zero when a regression exceeds the threshold; `--ci` is accepted
  for CI command compatibility.

Baselines should be committed when they represent the accepted quality bar for a
suite.

## Opik reporting

Local runs can use `--skip-opik`. When Opik reporting is enabled, metrics should
use `ScoreResult.metadata` for structured fields such as `difficulty`,
`mechanism`, `failure`, `suite_tier`, and `regression`.
Individual metric names use `{check_id}_{check_subject}` without a difficulty
suffix.

## Plugin authoring

Plugins add project-specific checks without changing core framework code. Read
[`PLUGIN_AUTHORING.md`](PLUGIN_AUTHORING.md) for the supported protocol and
examples. The current API version is documented in
[`PLUGIN_API_CHANGELOG.md`](PLUGIN_API_CHANGELOG.md).

## Calibration validation

Calibration validation checks whether generated low, medium, and high difficulty
acceptance criteria behave as intended:

```bash
python3 eval/validate_calibration.py --suite eval/suites/ --runs 3 --output eval/suites/calibration_report.json
```

Later phases will produce deterministic classifications and advisory synthesis
hints without auto-applying changes.

## Committed artifacts

Recommended committed artifacts:

- dataset manifests: `eval/datasets/*.yaml`
- dataset locks: `eval/datasets/*.lock`
- suite manifests and tiered story YAML: `eval/suites/**`
- server-compatible story JSON: `eval/stories/*.json`
- accepted baselines: `eval/.baselines/*.json`
- plugins: `eval/plugins/*.py`

Raw synthesis records, local run logs, and exploratory reports may be omitted
unless they are needed to explain a baseline or reproduce a release decision.

## Troubleshooting

- **Missing dataset**: check the path passed to `--dataset` and ensure the file
  is committed.
- **Schema drift**: re-run `init_dataset.py` and review lock-file changes before
  updating suites.
- **Plugin API mismatch**: update the plugin `api_version` or adapt it to the
  current protocol in `PLUGIN_API_CHANGELOG.md`.
- **Command check timeout**: raise the check timeout only after confirming the
  command is deterministic and scoped to the target repository.
- **Unsupported source connector**: install the documented optional dependency
  or switch to a supported connector.
- **Missing Opik credentials**: use `--skip-opik` for local deterministic runs or
  configure Opik before running in reporting mode.
- **Regression exits**: inspect the composite score, per-tier scores, failed
  check metadata, and baseline before using `--update-baseline`.

## CI guidance

CI should install requirements, validate manifests, run a representative suite
with `--skip-opik` or configured Opik credentials, compare against committed
baselines, and upload run artifacts. Keep live LLM, credentialed Opik, and
external dataset access out of unit tests; use deterministic fixtures instead.
