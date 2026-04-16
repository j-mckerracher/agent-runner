# Agent Runner — Broad Architecture & Evaluation Strategy

## Executive summary

Agent Runner is evolving from a local workflow orchestrator into one piece of a
three-part experimental apparatus whose purpose is to let changes to agents,
prompts, tools, and workflows be evaluated with scientific confidence. The
three parts are an **Orchestrator** (the runner proper), an **Agent Registry**
(versioned source of truth for agent definitions), and an **Evaluation
Harness** (task corpus, hermetic run execution, grading, lineage, baselines).
They live in a single monorepo with strict package boundaries and are
independently versionable. Runs execute inside ephemeral containers against
pinned substrate commits, with all LLM and HTTP traffic routed through a
gateway that supports record/replay. Grading combines deterministic checks
with a rubric-driven LLM judge that is pinned to a different model than any
worker agent. The primary design commitments are reproducibility, clean
separation of concerns, and lineage completeness; the primary tradeoffs
accepted are upfront infrastructure cost and a one-time migration of
acceptance criteria into a dual format.

---

## 1. Problem statement

Evaluation today runs against a live, drifting codebase with uncontrolled
stochasticity, ad-hoc grading, no versioned task corpus, and no baseline.
This makes it impossible to distinguish genuine improvements from
environmental noise. The problem is not the runner's code quality — it is the
absence of a controlled experimental apparatus around it. Any solution must
therefore address substrate stability, stochasticity control, grading
methodology, versioning of every input to a run, and lineage of every output.

---

## 2. Architectural goals

1. Reproducibility of any past run, subject only to residual LLM
   stochasticity that is itself quantified.
2. Independent versioning of runner, agents, tasks, harness, and judge.
3. Full lineage: every run uniquely identified by the tuple
   `(runner_version, agent_versions, task_version, substrate_commit,
   model_versions, cassette_id, seed)`.
4. Statistical evaluation (K-run) for LLM/prompt/tool changes; cassette
   replay for non-LLM changes.
5. Structured task corpus with calibrated difficulty and dual-format
   acceptance criteria (deterministic checks + rubric).
6. Clean separation between orchestration, agent definitions, and
   evaluation concerns.
7. Migration path from today's structure without breaking current thin CLI
   entrypoints.
8. Local-first operation, GCP-portable later.

## 3. Non-goals

- Not a general-purpose agent framework.
- No UI or dashboard in the initial scope; CLI + JSON + static HTML reports.
- Not online or low-latency evaluation; offline/batch.
- Not a merge-gating system initially; informational output only.
- Not multi-user today; designed to be multi-user-capable later.
- Not custom telemetry infrastructure; reuse Langfuse and structured logs.

## 4. Guiding principles

- **Hermetic by default, fast by opt-in.** Authoritative runs are container-
  isolated; a documented "dev mode" exists for inner-loop iteration and is
  explicitly non-authoritative.
- **Everything a run depends on is versioned.** If it can change, it is
  pinned and recorded.
- **The judge is not a worker.** The evaluating model is distinct from every
  worker model and treated as a baseline anchor; changing it is a rebaseline
  event.
- **Materialization over location coupling.** Agents live in a registry as
  source of truth and are materialized into `.claude/agents` at run start to
  satisfy Claude Code's fixed location constraint.
- **Separation of orchestration and evaluation.** The orchestrator knows
  nothing about evaluation; the harness knows the orchestrator only by its
  command-line and event contracts.
- **Lineage is non-negotiable.** Every run persists enough to be re-analyzed
  or re-graded later without re-execution.
- **One change type, one test scope.** Each category of change has an
  explicit, documented re-evaluation scope (see §13).

---

## 5. Current-state summary

- Three thin public entrypoints: `run.py`, `run_headless.py`, `run_general.py`.
- Orchestration logic in `agent_runner/` covers stage ordering, retries,
  evaluator feedback loops, logging, `##EVENT##` stdout lines, dry-run
  synthesis, pause/resume.
- Agents live in `.claude/agents`, coupled to Claude Code's expected path.
- Workflow stages: intake → task generation/eval → assignment/eval →
  software engineering/eval → QA/eval → lessons optimization.
- Observability: colored console logs, structured events, per-run JSON logs,
  optional Langfuse.
- Testing: unit-style runner tests plus a hand-calibrated acceptance-criteria
  set (GPT-5 Mini × 5 runs × 50% target pass rate). Acceptance criteria today
  are natural-language bullets embedded in ADO work items.
- No task corpus discipline, no baseline, no lineage across runs, no
  enforced reproducibility boundary.

## 6. Target-state vision

A monorepo of three cooperating subsystems with strict package boundaries:

```
agent-runner/                        (monorepo root)
├── packages/
│   ├── runner/                      Orchestrator (successor to agent_runner/)
│   ├── registry/                    Agent Registry + materialization
│   ├── harness/                     Evaluation Harness (new)
│   └── shared/                      Cross-cutting: models, events, logging
├── agent-sources/                   Source of truth for agent definitions
├── task-corpus/                     Versioned task corpus
├── substrates/                      Pinned substrate manifests (SHA refs)
├── cassettes/                       Recorded LLM/HTTP traffic
├── baselines/                       Per-task pass-rate bands and summaries
├── runs/                            Run archives (tiered retention)
└── bin/ (run.py, run_headless.py, run_general.py — thin shims preserved)
```

Every authoritative run is a container execution whose inputs are fully
pinned and whose outputs are fully archived. Non-authoritative dev-mode runs
bypass containerization for speed but are flagged as such in their lineage.

---

## 7. System boundaries

| Inside the system | Outside the system |
|---|---|
| Orchestrator, Registry, Harness, shared libraries | Claude Code itself |
| Task corpus, substrates manifest, cassettes, baselines, run archives | LLM providers (Anthropic, OpenAI, etc.) |
| Run container images | Azure DevOps, Discord, Langfuse |
| LLM/HTTP gateway (record/replay proxy) | Docker daemon / OS |
| Judge model configuration | The user's host filesystem outside run-scoped paths |

External systems are reached only through the gateway; there is no direct
call path from inside a run to the outside world.

## 8. External dependencies and integrations

- **Claude Code** — the agent runtime. Invoked by the orchestrator.
- **LLM providers** — accessed through the gateway. Models pinned by version.
- **Azure DevOps** — source of task intake. Calls gatewayed for replay.
- **Discord** — escalation/notifications. Gatewayed.
- **Langfuse** — optional tracing. Not on the authoritative data path.
- **Docker** — required for authoritative runs.

## 9. Major components

1. **Orchestrator (`packages/runner`).** Owns workflow definition, stage
   ordering, retry policy, evaluator loops, pause/resume, dry-run
   synthesis, structured event emission, per-run JSON logging. Agent-
   framework-agnostic at its interface; Claude Code is the current concrete
   driver. Emits a well-defined event stream and writes artifacts to a run-
   scoped directory provided by the caller.

2. **Agent Registry (`packages/registry` + `agent-sources/`).** Source of
   truth for agent definitions. Each agent version is an immutable bundle
   of prompt, tool list, config, and manifest, addressable as
   `name@version`. Exposes a materialization operation that writes a
   selected set of `name@version` bundles into a target `.claude/agents`
   directory inside a run's working copy.

3. **Evaluation Harness (`packages/harness`).** Owns the task corpus,
   substrate pinning, run scheduling (serial or parallel), container
   lifecycle, gateway configuration (record vs. replay), grading
   (deterministic + judge), baseline management, lineage recording, and
   regression reports. Invokes the orchestrator as an opaque subprocess.

4. **Shared (`packages/shared`).** Data models, event contracts, logging
   helpers, and the small interface types that runner and harness agree on.

5. **LLM/HTTP Gateway.** A proxy the orchestrator's runs point at. In
   record mode, forwards live traffic and writes cassettes keyed by
   rendered-prompt hash. In replay mode, serves from cassette and fails
   closed on cassette misses.

6. **Thin CLIs (`run.py`, `run_headless.py`, `run_general.py`).** Preserved
   as user-facing entrypoints; re-implemented as shims over the new
   orchestrator API.

## 10. Major responsibilities

| Concern | Owner |
|---|---|
| Workflow ordering, retries, evaluator loops | Runner |
| Stage-local normalization (intake etc.) | Stage-local agents, invoked by Runner |
| Agent version resolution and materialization | Registry |
| Task corpus curation and difficulty calibration | Harness |
| Hermetic run execution (container lifecycle) | Harness |
| LLM/HTTP record/replay | Gateway (driven by Harness) |
| Grading (deterministic + judge) | Harness |
| Baseline maintenance and regression detection | Harness |
| Run archiving and lineage | Harness |
| Structured event emission | Runner |
| Cross-run analysis and reporting | Harness |
| Notifications, dashboards | Out of scope initially |

## 11. Control flow (high level)

1. User or CI invokes a harness command: "evaluate change X against corpus
   Y" or "run task T once for debugging."
2. Harness resolves: runner version, agent versions, task version,
   substrate commit, model versions, cassette policy, seed, K (run
   repetitions). All recorded as the run's lineage header.
3. Harness builds or reuses a pinned container image. Inside the
   container, for each run:
   a. Extract pinned substrate commit into a fresh working copy.
   b. Materialize agent bundles from registry into `.claude/agents`.
   c. Start the LLM/HTTP gateway in the configured mode.
   d. Invoke the orchestrator subprocess with run-scoped paths.
   e. Orchestrator executes the workflow, emits events, writes artifacts.
   f. On exit, harness collects artifacts, events, logs, cassette deltas.
4. After all K runs complete, harness grades each run (deterministic first,
   judge second) and writes a per-run grade record.
5. Harness aggregates into a per-task statistic and compares to the
   per-task baseline band. Writes a regression report.
6. All of the above is persisted under `runs/<run-id>/`; summaries are
   written to `baselines/` per the retention policy.

## 12. Data flow (high level)

- **Inputs to a run:** pinned substrate + pinned agent bundles + task spec
  + model pins + cassette (or live) + seed.
- **Outputs of a run:** stage artifacts, event stream, per-run JSON log,
  LLM/HTTP trace (cassette), token/cost metrics, final artifact,
  deterministic check results, judge verdicts, lineage header.
- **Outputs of an evaluation cycle:** aggregated pass-rates per task,
  comparison to baseline, regression report, archived run bundle.

---

## 13. Testing philosophy

- **Evaluation is tiered.** Three layers, each answering a different
  question:
  1. Unit-agent tests — does a single agent produce valid output on
     canned inputs? Fast, cheap, cassette-replay-heavy.
  2. Stage tests — does a stage + its evaluator loop converge correctly
     on a curated set of inputs? Medium cost.
  3. Workflow tests — does the full workflow produce a correct final
     artifact for a calibrated task? Expensive, statistical.
- **Tests and change types are matched explicitly.** See mapping below.
- **Grading is dual-format.** Deterministic checks come first and are
  authoritative where they apply. The rubric-driven judge provides
  structured scores where determinism is not feasible. The judge is a
  pinned model distinct from any worker model.
- **Stochasticity is handled per regime.**
  - Non-LLM code changes → cassette replay, single-shot, deterministic.
  - LLM/prompt/tool changes → K-run live, statistical comparison.
  - Judge model bump → full re-grade of archived runs; treated as a
    baseline reset.
- **Baselines are per-task pass-rate bands.** A regression is a
  statistically significant deviation from a task's historical band,
  not a single-run delta.
- **Change type → re-evaluation scope (authoritative):**

| Change | Scope | Mode |
|---|---|---|
| Orchestrator/runner code | Full corpus | Cassette |
| Single agent's prompt | Full corpus | Live, K=5 |
| Tool definition | Full corpus | Live if tool is agent-invoked; cassette otherwise |
| Workflow composition | Full corpus | Live, K=5 |
| Task added to corpus | New task only | Calibration (5 runs, 50% target) |
| Evaluator rubric | Re-grade archives | No re-execution |
| Judge model bump | Re-grade archives | Baseline reset |

## 14. Environment strategy

- **Authoritative runs** execute in an ephemeral container per run, from a
  pinned image digest. The container extracts a pinned substrate commit,
  materializes agents from the registry, starts the gateway, runs the
  orchestrator, collects outputs, and exits.
- **Dev mode** skips the container for inner-loop speed. Dev-mode runs are
  flagged in their lineage and excluded from baseline updates.
- **Substrates are pinned by commit SHA.** The `substrates/` directory is
  a manifest, not a copy; actual substrates are fetched on demand and
  cached by SHA.
- **Initial substrate corpus** may be a single pinned SHA of an existing
  real codebase — this is where the user's "frozen copy" instinct is
  correct, subject to being only one input among many.
- **Synthetic fixture substrates** are added later for targeted stage or
  failure-mode tests.
- **Parallelism** is supported: N concurrent containers on the laptop,
  bounded by CPU/memory. The design is parallel-first even when run
  serially.

## 15. Observability and monitoring strategy

- **Four data planes:**
  1. Structured events on stdout (`##EVENT##` lines) — preserved.
  2. Per-run JSON log — preserved, extended with lineage header.
  3. Gateway trace (cassette) — the complete LLM/HTTP record of a run.
  4. Grading record — deterministic results + judge verdicts.
- **Langfuse remains optional** and off the authoritative path. Its role
  is exploratory/debugging, not evaluation of record.
- **Retention is tiered.** Full run archives kept for the most recent N
  evaluation cycles. Older archives collapsed to per-run summaries plus
  lineage header. Baselines and lineage headers kept indefinitely.
- **Every run answers: "what was run, against what, how did it behave,
  what did it produce, how was it graded?"** — from lineage alone, no
  re-execution required.

## 16. Maintainability strategy

- **Strict package boundaries.** Runner does not import from harness and
  vice versa; both import only from shared. Enforced by import-linter or
  equivalent.
- **Stable interfaces over stable internals.** The orchestrator's CLI +
  event contract is the contract; its internals are free to evolve.
- **One reason to change per package.** Runner changes ↔ orchestration
  logic. Registry changes ↔ agent versioning mechanics. Harness changes
  ↔ evaluation methodology. Agent changes ↔ `agent-sources/` only.
- **Tests at the boundary.** Each package is tested through its public
  interface; cross-package tests are few and explicit.

## 17. Repository and code-organization implications

- **Preserve:** thin CLI entrypoints; existing event contract; stage
  concept and stage-local intake pattern; Langfuse as optional tracing.
- **Tighten:** stage-ordering logic lives only in one place in the
  runner; retries and evaluator loops become an explicit module;
  workflow definition becomes a first-class data object rather than
  implicit in code paths.
- **Restructure:** move evaluation concerns out of any place they leaked
  into runner code; introduce `packages/registry` and `packages/harness`;
  split models, artifacts, prompts, and runtime into `shared/` where they
  are cross-cutting.
- **Isolate:** agent definitions into `agent-sources/`, not `.claude/agents`
  as source of truth; gateway code behind a small interface so it can be
  swapped (record-mode proxy, replay-mode server, passthrough for dev).
- **Migration does not break current CLIs.** `run.py`, `run_headless.py`,
  `run_general.py` remain user-facing and functionally equivalent.

## 18. Operational model

- **Local-first.** All components run on the user's laptop. Docker is the
  only new hard dependency.
- **Commands are harness-level verbs:** `evaluate`, `calibrate`,
  `baseline`, `replay`, `report`, `materialize`, `record`.
- **Each command produces artifacts on disk that are the authoritative
  result.** No state lives only in memory or only in a dashboard.
- **GCP portability is preserved** by keeping the container as the unit
  of execution and the filesystem layout as the unit of state.

## 19. Tradeoffs being accepted

- Upfront infrastructure cost: container, gateway, registry materialization,
  dual-format acceptance criteria migration.
- A second model (the judge) whose cost is real, even if worker models are
  free today.
- Added conceptual surface area (three subsystems instead of one).
- Dev-mode existing as a second path, with documentation cost.
- Storage growth on the order of low GBs per evaluation cycle.

## 20. Major risks and unknowns

- **Judge-model drift.** A silent provider-side update to the judge would
  invalidate historical comparisons. Mitigated by version pinning and
  "any bump = rebaseline" discipline.
- **Cassette staleness.** Mitigated by keying cassettes on rendered-prompt
  hash so prompt changes cause deliberate cache misses.
- **Acceptance-criteria migration cost.** Non-trivial; handled
  incrementally per task over time, not in one pass.
- **Over-engineering risk.** The hybrid (container + gateway + registry +
  harness) is heavier than today. Mitigated by dev mode and by building
  incrementally.

## 21. Recommended architectural direction and rationale

**Direction:** build the three-subsystem monorepo described above, using
ephemeral containers per run over pinned substrate commits as the
authoritative environment, with a gateway-based record/replay for LLM and
HTTP traffic, a dual-format grading layer, and per-task pass-rate-band
baselines. Start with a single real-codebase substrate (honoring the user's
original "frozen copy" instinct as one input), and grow into a hybrid of
real and synthetic substrates as needs become clear.

**Rationale (compressed from the comparative evaluation):**

- Reproducibility, isolation from production drift, evaluation reliability,
  and regression detection all require enforced hermeticity — containers
  are the cheapest credible mechanism.
- Independent versioning of runner/agents/tasks/judge is required to make
  any comparison across time meaningful.
- A tiered testing model and explicit change-type → scope mapping make
  evaluation cost-proportional to the change being evaluated.
- The approach is GCP-portable, CI-compatible, and parallel-first without
  being premature.
- Dev mode preserves local developer ergonomics without contaminating
  authoritative results.

**Assumptions that must remain true:** judge model (GPT-5.4 high) remains
available and pinnable; acceptance-criteria migration proceeds incrementally;
substrate inputs are git-addressable; single-user today with team-capable
design.

The detailed architecture document elaborates this direction into subsystem
decomposition, interfaces, artifact schemas, reproducibility mechanics,
evaluation methodology, and migration sequencing.
