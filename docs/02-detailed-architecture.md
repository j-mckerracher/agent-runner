# Agent Runner — Detailed Architecture

This document elaborates the direction set in `01-broad-architecture.md`. It
assumes familiarity with that document's terminology (Orchestrator, Agent
Registry, Evaluation Harness, Gateway, substrate, cassette, lineage header,
baseline band). Where this document and the broad document disagree, the
broad document is authoritative for intent; this document is authoritative
for mechanics.

---

## 1. Architecture decomposition by subsystem

### 1.1 Orchestrator (`packages/runner`)

Internal modules:

- `workflow/` — workflow definitions as data (stages, evaluator pairings,
  retry policies, escalation rules). No imperative ordering code.
- `engine/` — the executor that consumes a workflow definition and drives
  it. Owns stage sequencing, retries, evaluator loops, pause/resume.
- `agents/` — the agent-invocation abstraction. One concrete driver
  today (`ClaudeCodeDriver`); others pluggable.
- `artifacts/` — artifact read/write, schema validation, stage-boundary
  handoff.
- `events/` — structured `##EVENT##` emission and per-run JSON logging.
- `runtime/` — execution context, paths, cancellation, subprocess control.
- `cli/` — thin entrypoints; implementations of `run.py`, `run_headless.py`,
  `run_general.py` as shims.

Key properties:

- Knows nothing about evaluation, baselines, cassettes, or containers.
- Receives all paths (working copy, artifact dir, event log target, agent
  dir) as inputs; never infers them.
- Deterministic with respect to its inputs, modulo the LLM call boundary.

### 1.2 Agent Registry (`packages/registry` + `agent-sources/`)

- `agent-sources/<name>/<version>/` — immutable bundle: `prompt.md`,
  `tools.json`, `config.yaml`, `manifest.yaml`.
- `packages/registry/` — loader, resolver, and materializer.
- A **materialization** operation takes a set of `name@version` refs and a
  target directory, writes the bundles into `<target>/.claude/agents/<name>/`
  in the layout Claude Code expects, and records a materialization
  manifest (the exact versions written).
- Version identifiers are human-readable and immutable (e.g., `v3`,
  `2025-04-10a`). Content hashes are computed and stored alongside.

### 1.3 Evaluation Harness (`packages/harness`)

Internal modules:

- `corpus/` — task loading and validation.
- `substrates/` — substrate manifest resolution and caching.
- `scheduler/` — serial or parallel execution of runs, concurrency control.
- `container/` — image build/pull, container lifecycle, volume mounts.
- `gateway/` — start/stop, record/replay mode wiring.
- `grading/` — deterministic checks and judge invocation.
- `baseline/` — per-task pass-rate band storage and comparison.
- `lineage/` — lineage header construction and persistence.
- `reporting/` — human- and machine-readable run and cycle reports.
- `cli/` — harness verbs: `evaluate`, `calibrate`, `baseline`, `replay`,
  `report`, `materialize`, `record`.

### 1.4 Shared (`packages/shared`)

- Data models (Pydantic or dataclasses): `Task`, `TaskVersion`,
  `AcceptanceCriterion`, `AgentRef`, `RunLineage`, `StageArtifact`,
  `EventRecord`, `GradingRecord`, `BaselineBand`.
- Event contract and version constant.
- Logging helpers and path utilities.
- Nothing here may import from runner or harness.

### 1.5 Gateway

- A small HTTP proxy started by the harness before the orchestrator
  subprocess begins.
- Three modes: `live` (passthrough), `record` (passthrough + persist),
  `replay` (serve from cassette, fail closed on miss).
- Intercepts: LLM endpoints (per-provider), ADO REST, Discord webhooks.
- Keying policy: cassette entries keyed by
  `hash(provider, endpoint, canonicalized_request_body)` where
  canonicalization strips timestamps, request IDs, and equivalent noise
  but preserves rendered prompts verbatim.

---

## 2. Component responsibilities (summary table)

| Component | Owns | Does not own |
|---|---|---|
| Runner.engine | Stage ordering, retries, evaluator loops, pause/resume | Evaluation, containers, cassettes |
| Runner.agents | LLM-framework driver abstraction | Agent definitions |
| Registry | Agent version storage, resolution, materialization | Agent runtime behavior |
| Harness.scheduler | Run concurrency, lifecycle | Stage logic |
| Harness.grading | Deterministic checks, judge invocation | Run execution |
| Harness.baseline | Pass-rate bands, regression detection | Grading semantics |
| Gateway | LLM/HTTP record/replay | Grading |
| Shared | Cross-cutting types and contracts | Any logic |

---

## 3. Interfaces and contracts

### 3.1 Runner CLI contract (the harness depends only on this)

```
runner-exec \
  --workflow <workflow-id>               # e.g. "standard" or "general"
  --task-spec <path-to-task-json>
  --working-copy <path>                  # fresh extracted substrate
  --agents-dir <path>                    # already materialized by caller
  --artifact-dir <path>                  # empty dir, runner writes here
  --event-log <path>                     # runner appends JSON-lines
  --json-log <path>                      # per-run structured log
  --seed <int>
  --model-config <path>                  # pinned model identifiers
  --gateway-url <url>                    # LLM/HTTP traffic target
  [--dry-run] [--pause-on <stage>]
```

Exit codes: `0` normal, `2` workflow failure with artifacts, `3` infra
failure without artifacts, `4` canceled. Any other code is an infra bug.

### 3.2 Event contract

Every `##EVENT##` line is a single JSON object with required fields:

```
{
  "event_version": "1",
  "ts": "<ISO-8601>",
  "run_id": "<uuid>",
  "stage": "<stage-id | null>",
  "kind": "<stage.start|stage.end|eval.pass|eval.fail|retry|escalate|artifact.write|...>",
  "data": { ... }
}
```

Event-version bumps are major events; harness tolerates only known
versions and fails closed on unknown.

### 3.3 Artifact schema boundaries

- Each stage's output artifact is a JSON file with a `$schema` reference
  into `packages/shared/schemas/`.
- Schemas are versioned independently from code and from workflows.
- Cross-stage contracts are expressed as schema references, not ad-hoc
  dict shapes.

### 3.4 Registry materialization contract

```
materialize(
  agents: list[AgentRef],            # [("intake", "v3"), ...]
  target_working_copy: Path
) -> MaterializationManifest
```

Writes under `target_working_copy/.claude/agents/<name>/` and returns
a manifest listing exact versions + content hashes.

### 3.5 Gateway cassette format

- One directory per cassette: `cassettes/<cassette-id>/`.
- `index.json` lists entries with keys, timestamps, and file refs.
- Each entry is a pair of `request.json` and `response.json`.
- A cassette is immutable once sealed; record-mode runs produce new
  cassettes, never modify existing ones.

---

## 4. Workflow decomposition

Workflows are declared as data:

```yaml
id: standard
version: 7
stages:
  - id: intake
    agent: intake@v3
    evaluator: null
    retry: { max: 1 }
  - id: task_gen
    agent: task_gen@v4
    evaluator: task_eval@v4
    retry: { max: 3, backoff: "immediate" }
  ...
```

Stages reference agents by `name@version`; the engine resolves them via
the Registry at execution start. The workflow definition is part of run
lineage.

---

## 5. Lifecycle of a run

1. **Resolution.** Harness builds a `RunLineage` from CLI arguments and
   config: runner version, workflow id+version, agent refs, task version,
   substrate commit, model pins, cassette mode, seed, K.
2. **Pre-flight.** Harness verifies all pins resolve; unpinned inputs are
   errors in authoritative mode and warnings in dev mode.
3. **Image.** Harness selects a container image by pinned digest; builds
   on cache miss.
4. **Substrate extraction.** Fresh volume; `git archive` of pinned SHA
   into it, or download of a pre-packaged tarball.
5. **Registry materialization.** Agents materialized into the fresh
   working copy's `.claude/agents/`.
6. **Gateway start.** Proxy started in configured mode, pointed at a
   cassette path.
7. **Runner invocation.** Subprocess per the CLI contract; all paths
   run-scoped.
8. **Run execution.** Workflow executes; events streamed; artifacts
   written; gateway records or replays.
9. **Runner exit.** Harness collects outputs, seals cassette (in record
   mode), stops gateway.
10. **Grading.** Deterministic checks first; judge only for criteria not
    covered deterministically.
11. **Aggregation.** After all K runs complete, harness computes per-task
    pass rate and compares to baseline band.
12. **Archive.** Under `runs/<run-id>/`, harness writes lineage header,
    event log, JSON log, artifacts, grading records, cassette ref,
    aggregation summary.

---

## 6. Artifact model and artifact boundaries

- **Stage artifact** — JSON file written by the orchestrator at stage end;
  validated against its schema; its path is emitted as an
  `artifact.write` event.
- **Run artifact bundle** — the union of all stage artifacts plus the
  final output, collected under `runs/<run-id>/artifacts/`.
- **Event log** — `runs/<run-id>/events.jsonl`.
- **JSON log** — `runs/<run-id>/run.json`.
- **Grading record** — `runs/<run-id>/grading.json`.
- **Cassette reference** — path into `cassettes/` plus hash.
- **Lineage header** — `runs/<run-id>/lineage.json`, self-describing and
  sufficient to regenerate the run's inputs (though not the LLM
  responses unless the cassette is kept).
- Boundary rule: artifacts are produced only by the runner; grading and
  lineage only by the harness. They never cross-write.

---

## 7. Configuration strategy

- **Three config layers:** user defaults (`~/.agent-runner/config.yaml`),
  repository defaults (`config/default.yaml`), per-command CLI flags.
  Later overrides earlier.
- **All pins live in config or CLI,** never in code. The set of pins: runner
  version (git tag or SHA), workflow id+version, agent refs, task version,
  substrate commit, model identifiers, judge model identifier, cassette
  mode, seed, K.
- **Dev-mode overrides** are explicit flags (`--dev-mode`) and are stamped
  into lineage; they disqualify the run from baseline updates.
- **Secrets** (provider keys) live only in env vars; never logged; never
  written to lineage.

---

## 8. Versioning strategy

| Artifact | Versioning | Immutability | Discovery |
|---|---|---|---|
| Runner | Semver git tags | Immutable | `runner --version` |
| Workflow definition | Monotonic int per id | Immutable | `workflow.yaml` |
| Agent bundle | Human-readable label | Immutable | Registry manifest |
| Task | Monotonic int per task | Immutable | `task.yaml` |
| Substrate | Git SHA | Immutable | Substrate manifest |
| Model | Provider identifier | Provider-controlled; pin + record in lineage |
| Judge model | Provider identifier | Pinned; change = rebaseline |
| Cassette | Content hash | Immutable once sealed | `cassettes/index` |
| Event contract | Single int | Breaking bumps only | `shared/events.py` |
| Artifact schemas | Semver per schema | Backward-compatible bumps allowed | `$schema` refs |

---

## 9. Environment provisioning strategy

- **Container image** built from `docker/runner.Dockerfile`. Pinned base
  image digest. Includes Python, Node if needed, Claude Code CLI, git,
  and gateway client.
- **One container per run.** No sharing. Destroyed on exit.
- **Volumes:** a run-scoped tmpfs mount for the working copy; a read-only
  mount for agent-source materialization; a writable mount for the
  artifact directory.
- **Network:** container reaches only the gateway; no direct egress.
- **Resource limits:** CPU and memory caps per container; harness
  concurrency cap ensures laptop stays responsive.
- **Dev mode:** same process, same paths, no container; enforced by
  lineage flag only.

---

## 10. Reproducibility strategy

- **Every input pinned and recorded.** If it varies, it is in the lineage.
- **Cassette replay** provides bitwise-determined LLM responses for any
  non-prompt-changing re-run.
- **Seeds** used anywhere an RNG is available (Python `random`, stage-
  local tools). Time-based inputs wrapped so they can be frozen under
  test.
- **Dual-format acceptance criteria** make deterministic checks first-
  class; the judge is consulted only for the residual.
- **Run ID is a UUID**; `run-<uuid>` is the global identifier. Two runs
  with identical lineage except for the UUID are intended to produce
  identical outputs under replay.

---

## 11. Test-fixture and test-repository strategy

- **`task-corpus/`** — one directory per task:
  - `task.yaml` — metadata, difficulty class, substrate ref, agent set,
    acceptance criteria (dual format).
  - `criteria/deterministic/` — executable checks (scripts, test
    invocations).
  - `criteria/rubric/` — rubric items for the judge.
- **`substrates/`** — a manifest listing pinned substrate commits and
  their sources (local path, URL, or git ref).
- **Initial substrate** may be a pinned SHA of a real codebase, honoring
  the original "frozen copy" instinct. Additional substrates added over
  time: synthetic fixtures for targeted stage tests, secondary real
  codebases for domain coverage.
- **Calibration artifact:** each task retains its calibration record
  (5 runs, GPT-5 Mini, 50% target), stored alongside the task.

---

## 12. Evaluation methodology

### 12.1 Change-type → scope (authoritative)

| Change | Corpus scope | Execution mode | Baseline comparison |
|---|---|---|---|
| Runner code | Full | Cassette | Per-task band |
| Agent prompt | Full | Live, K=5 | Per-task band |
| Tool definition | Full | Live if agent-invoked, else cassette | Per-task band |
| Workflow composition | Full | Live, K=5 | Per-task band |
| Task added | New task only | Calibration (5 × 50%) | Initializes band |
| Evaluator rubric | Archived runs | Re-grade only | Old vs. new grades |
| Judge model bump | Archived runs | Re-grade only | Full rebaseline |

### 12.2 Per-run grading

1. **Deterministic pass.** Run each criterion script; collect pass/fail.
   Criteria coverage is recorded.
2. **Judge pass.** For rubric items, invoke **GPT-5.4 high** (the pinned
   judge model) with a canonical prompt template. Judge output is a
   structured rubric score per item.
3. **Overall verdict.** Boolean pass if every deterministic criterion
   passes AND every rubric item meets its threshold. Otherwise fail,
   with a categorized failure reason.

### 12.3 Per-task aggregation

- Pass rate = passes / K.
- Compared to the task's baseline band (mean ± band width, established
  by calibration).
- Regression = pass rate below band by a configurable significance.
- Improvement = pass rate above band by a configurable significance.
- Cycle report lists regressions, improvements, and unchanged tasks.

## 13. Baseline strategy

- **Baseline storage:** `baselines/<task-id>/` with a JSON file per
  baseline event (initial calibration, judge-model rebaseline,
  deliberate re-baseline).
- **Active baseline:** the most recent event is active; older ones
  retained for historical analysis.
- **Band derivation:** from the K-run pass rate distribution at baseline
  event time. Default band width: ±15 percentage points, tunable per
  task.
- **Who updates baselines:** only the harness `baseline` command, with
  an explicit reason recorded. Never automatic.

## 14. Regression detection strategy

- Per-task comparison of current cycle's pass rate to active baseline
  band.
- Cycle-level report categorizes tasks as `regressed`, `improved`,
  `unchanged`, or `insufficient-data`.
- Regression does not block anything today (non-goal: gating). Reports
  are saved for human review.
- Trend analysis across cycles is a future extension.

## 15. Scoring / rubric / evaluator strategy

- **Deterministic checks** are first-class and preferred wherever a
  criterion can be expressed as a script or test assertion.
- **Rubric items** for the judge are templated:
  - criterion statement,
  - pass threshold,
  - scoring scale (e.g., 0–3),
  - judge prompt fragment,
  - examples (optional).
- **Judge prompt** is version-controlled in `packages/harness/grading/prompts/`.
- **Judge model pin** is a first-class config and lineage field.
  **Current pin: GPT-5.4 high.** Any change to this model is a
  rebaseline event — all historical grading results become incomparable.
- **Human override** of a judge verdict is supported via a `grading-
  override.json` alongside the grading record; overrides are themselves
  part of lineage.

## 16. Observability details

| Plane | Medium | Retention | Purpose |
|---|---|---|---|
| Events | `events.jsonl` | Full: current cycle + last N cycles; thereafter summary | Machine consumers |
| JSON log | `run.json` | Same as events | Human inspection |
| Cassette | `cassettes/<id>/` | Content-hash deduplicated; pruned by LRU | Reproducibility, replay |
| Grading | `grading.json` | Indefinite | Baseline / regression |
| Lineage | `lineage.json` | Indefinite | The record of record |
| Langfuse traces | External | Per Langfuse retention | Debugging only |
| Console logs | Stdout/stderr | Captured per run | Developer feedback |

Event contract evolution rules:

- Additive fields are non-breaking; unknown-field policy is "ignore and
  pass through."
- Changing or removing a field requires an `event_version` bump.
- Harness rejects runs emitting an unknown major event version.

## 17. Failure handling and retry considerations

- **Retries are workflow-internal.** The runner retries per stage policy;
  the harness never retries a runner failure.
- **Infra failure** (container crash, gateway crash, disk full) produces
  a run marked `infra-failed` and excluded from aggregation. Harness may
  re-attempt up to a configurable cap.
- **Flaky cassettes** (replay miss in replay mode) fail the run. The
  remedy is deliberate re-recording, not silent fallback.
- **Partial runs** (orchestrator exited with partial artifacts) are
  graded as failures with a categorized reason.
- **Judge unavailability** fails grading with a distinct reason; the run
  artifacts are retained; re-grading is a first-class operation.

## 18. Migration and rollout strategy (phased, unsequenced by time)

Phases, each independently valuable:

**Phase A — Seams without behavior change**

- Introduce `packages/shared` and move cross-cutting types into it.
- Define and publish the Runner CLI contract and the Event contract at
  version 1.
- Leave behavior of `run.py`, `run_headless.py`, `run_general.py` exactly
  as is; they become shims.

**Phase B — Registry as source of truth**

- Add `agent-sources/` and `packages/registry`.
- Implement materialization.
- Stop treating `.claude/agents` as source of truth; keep it as a
  materialization target.
- Update CLIs to materialize before invoking the engine.

**Phase C — Harness skeleton**

- Add `packages/harness` with `evaluate`, `report`, and `materialize`
  verbs.
- Initial runs are non-containerized (dev mode); lineage is still
  recorded in full.
- Task corpus bootstrapped from existing calibration tasks.

**Phase D — Hermeticity**

- Container image and per-run container execution.
- Gateway introduced in live-only mode first.

**Phase E — Record/replay**

- Cassette recording on.
- Replay mode enabled for runner-code changes.
- Cassette keying policy enforced.

**Phase F — Baselines and regressions**

- Per-task bands computed from calibration.
- Cycle reports with regression categorization.

**Phase G — Judge hardening**

- Judge model pinned to a distinct provider/model.
- Rubric authoring migrated into `criteria/rubric/`.
- Rebaseline procedure documented and exercised.

**Phase H — Dual-format AC migration**

- Incrementally split natural-language ACs into deterministic + rubric
  pairs. Not blocking other phases; proceeds per task over time.

Backward compatibility: the three thin CLIs remain functional throughout.
Any existing automation that consumes the event stream keeps working
because event version 1 is preserved.

## 19. Implementation sequencing principles

- Every phase ships a working system.
- No phase depends on a later phase's behavior (only on earlier phase's
  seams).
- Dev mode is available from Phase C and remains available forever.
- Authoritative mode becomes available at Phase D and mandatory for
  baseline-updating cycles at Phase F.

## 20. Open implementation questions

O1. ~~**Registry physical form.**~~ **Resolved:** sibling directory
    (`agent-sources/`) in the monorepo. Promote to separate repo when a
    second consumer of the registry exists.

O2. **Cassette canonicalization.** Strictness of request-body
    canonicalization. Proposal: strip only obvious volatile fields
    (timestamps, request IDs, nonces); preserve rendered prompts byte-
    exact.

O3. **ADO/Discord record/replay depth.** Whether to cover all endpoints
    or only read paths initially.

O4. **K default.** Proposal: K=5 for full corpus cycles, matching
    existing calibration; configurable per-task override.

O5. **Baseline band width.** Proposal: ±15 points default; revisit
    after first full cycle has real data.

O6. **Storage tiering policy.** Full archives for N=5 most recent
    cycles; summaries forever; cassettes deduplicated across runs.

O7. **Parallel concurrency cap.** Start with `min(4, host_cpus/2)`.

O8. **Workflow-definition format.** YAML as proposed; validate with a
    JSON Schema stored in `shared/schemas/workflow.schema.json`.

O9. **Dev-mode tooling.** Whether to ship a `make dev-run` or similar
    affordance, or expect developers to invoke runner CLI directly.

O10. ~~**Judge model selection.**~~ **Resolved:** GPT-5.4 high. Pinned
     in config and recorded in every run's lineage. Must be distinct from
     all worker models. Model bump = full rebaseline required.

## 21. Recommended next decisions

In priority order:

1. Freeze the event contract at version 1 and the Runner CLI contract;
   document both in `packages/shared/contracts/`.
2. Draft the workflow-definition YAML schema (O8) and convert the
   current implicit workflow into its first instance.
3. Pick the first substrate pin and author a seed task corpus of 3–5
   calibrated tasks using the dual-format acceptance criteria pattern,
   even if most of the rubric stays LLM-graded initially.
4. Stand up the harness skeleton (`evaluate` and `report` verbs) in
   dev mode over this seed corpus before introducing containers.
5. Introduce the gateway and containers only after the dev-mode harness
   proves valuable; otherwise the cost is being paid before the benefit.

These decisions, taken in order, minimize the distance between each
commitment and its first observed benefit, and keep the system useful
at every intermediate state.
