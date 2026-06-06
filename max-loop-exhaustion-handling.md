# Implementation plan: max-loop exhaustion handling

## 1. Target behavior

When any evaluation optimizer loop reaches its max iteration count, the workflow should **stop that local loop**, but it should **not automatically fail** and it should **not silently continue**.

The new behavior should be:

```text
loop reaches max attempts
→ produce structured LoopResult(exhausted=True, passed=False)
→ persist evaluator output and loop metadata
→ run deterministic gates for that stage
→ run an automated exhaustion handler
→ choose a bounded recovery/override/failure action
→ continue only if the action proves the AC risk is acceptable
→ before PR/release, run a final acceptance gate over every AC
```

The important distinction:

```text
Max iterations exhausted ≠ acceptance criteria failed.
Max iterations exhausted = this local evaluator/producer pair did not converge within budget.
```

So the implementation should move the final decision out of the loop and into the orchestrator.

---

# 2. Current code facts that drive the plan

Based on the uploaded code, the relevant current behavior is:

1. `core/evaluator_optimizer_loops.py` has two loop functions:

   * `run_eval_optimizer_loop(...)`
   * `run_uow_eval_loop(...)`

2. Both loops currently detect pass with:

```python
passed = "PASS" in evaluator_out
```

This is unsafe because strings such as `"does not PASS"` or `"PASS criteria are not met"` can be misread as success.

3. Both loops emit `loop.end` with:

```python
exhausted=actual_iterations >= iter_count and not passed,
status="ok",
```

So an exhausted loop is still reported as `status="ok"`.

4. `run.py` calls `run_eval_optimizer_loop(...)` for:

   * task generation
   * task assignment
   * QA

5. `run.py` calls `run_uow_eval_loop(...)` for each UoW during execution.

6. After task generation, `run.py` only requires:

```python
planning/tasks.yaml
```

7. After task assignment, `run.py` only requires:

```python
planning/assignments.json
```

8. After QA, `run.py` currently does **not** require `qa/qa_report.yaml` before moving to PR review.

9. Existing deterministic gates exist in partial form, but they are not fully integrated into orchestration:

   * `core/check_gates.py`
   * `core/run_gates.py`
   * `agent-script-source/validate-artifact-schema.py`
   * `scripts/validate-artifact-schema.py`

10. There is a schema mismatch risk:

* `agent-definition-source/qa/v2/prompt.md` tells QA to write fields like `qa_status` and `acceptance_criteria_validation`.
* `agent-script-source/validate-artifact-schema.py` expects fields like `overall_status` and `ac_validations`.

That mismatch can cause evaluator loops to fail even when QA did useful work.

---

# 3. Guiding principles

The implementation should follow these rules.

## Rule 1: The loop max is a hard local limit

Never keep the same loop running forever.

```text
Do not:
while not pass:
    retry same producer/evaluator forever
```

Instead:

```text
Try the local loop up to N times.
If it fails, switch to a different automated decision/recovery mechanism.
```

## Rule 2: Exhaustion is not success

An exhausted loop must not return “ok” in a way that allows `run.py` to continue silently.

It should produce:

```python
LoopResult(
    passed=False,
    exhausted=True,
    status="exhausted",
    stop_reason="max_iterations",
)
```

## Rule 3: Exhaustion is not automatic failure

The handler must inspect evidence.

The workflow may continue after exhaustion only when it records a durable, evidence-backed decision such as:

```text
Evaluator failed, but deterministic gates passed, final AC coverage is complete,
and remaining issues are nonblocking warnings.
```

## Rule 4: Every evaluator issue must be handled

Every evaluator issue must end in one of these states:

```text
fixed
proven nonblocking
proven incorrect by stronger evidence
unresolved and blocking
```

Only the first three allow forward progress.

## Rule 5: Final acceptance criteria evidence is the release gate

The final question is not:

```text
Did every local evaluator say PASS?
```

The final question is:

```text
Does every acceptance criterion have planning, implementation, and verification evidence?
```

---

# 4. High-level architecture

Add five concepts to the workflow:

```text
LoopResult
ParsedEvaluation
DeterministicGateResult
LoopDecision
AcceptanceLedger
```

The flow becomes:

```text
run loop
  ↓
LoopResult
  ↓
if passed:
    continue
else if exhausted:
    run deterministic gates
    parse evaluator issues
    consult recovery budget
    choose LoopDecision
    apply decision
  ↓
before PR:
    build AcceptanceLedger
    run final acceptance gate
```

---

# 5. New files to add

## 5.1 `core/evaluation_contracts.py`

Create shared data classes and enums.

Suggested contents:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class LoopStatus(StrEnum):
    PASSED = "passed"
    EXHAUSTED = "exhausted"
    ERROR = "error"
    LEGACY = "legacy"


class LoopStopReason(StrEnum):
    PASSED = "passed"
    MAX_ITERATIONS = "max_iterations"
    STAGNATION = "stagnation"
    ERROR = "error"


class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class DecisionAction(StrEnum):
    CONTINUE = "continue"
    CONTINUE_WITH_EVIDENCE = "continue_with_evidence"
    REPAIR_SAME_STAGE = "repair_same_stage"
    RERUN_QA = "rerun_qa"
    CREATE_BUGFIX_UOW = "create_bugfix_uow"
    ROUTE_TO_UPSTREAM_STAGE = "route_to_upstream_stage"
    HUMAN_ESCALATION_REQUIRED = "human_escalation_required"
    FAIL_UNRESOLVED = "fail_unresolved"


@dataclass
class EvaluationIssue:
    issue_id: str
    severity: str
    category: str
    description: str
    location: str | None = None
    actionable_fix: str | None = None
    source: str = "evaluator"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.severity.lower() in {"critical", "high"}


@dataclass
class ParsedEvaluation:
    raw_text: str
    parsed: dict[str, Any] | None
    parse_errors: list[str] = field(default_factory=list)
    overall_result: str | None = None
    status: str | None = None
    passed: bool = False
    programmatic_gates: dict[str, Any] = field(default_factory=dict)
    issues: list[EvaluationIssue] = field(default_factory=list)
    blocking_issues: list[EvaluationIssue] = field(default_factory=list)
    nonblocking_issues: list[EvaluationIssue] = field(default_factory=list)
    escalation_required: bool = False
    escalation_reason: str | None = None
    final_verdict: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopResult:
    loop_name: str
    stage: str
    change_id: str
    passed: bool
    exhausted: bool
    status: str
    stop_reason: str
    actual_iterations: int
    max_iterations: int
    producer_output: str = ""
    evaluator_output: str = ""
    parsed_evaluation: ParsedEvaluation | None = None
    uow_id: str | None = None
    artifact_paths: list[str] = field(default_factory=list)
    evaluator_artifact_paths: list[str] = field(default_factory=list)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    issue_fingerprint: str | None = None
    duration_ms: int | None = None
    runner: str | None = None
    model: str | None = None

    # Backward compatibility: existing tests and callers can still unpack:
    # producer_out, evaluator_out = run_eval_optimizer_loop(...)
    def __iter__(self):
        yield self.producer_output
        yield self.evaluator_output


@dataclass
class GateResult:
    stage: str
    passed: bool
    gates: dict[str, bool]
    issues: list[EvaluationIssue] = field(default_factory=list)
    warnings: list[EvaluationIssue] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    artifact_paths: list[str] = field(default_factory=list)


@dataclass
class LoopDecision:
    action: str
    stage: str
    change_id: str
    reason: str
    result: str
    blocking_issues: list[EvaluationIssue] = field(default_factory=list)
    nonblocking_issues: list[EvaluationIssue] = field(default_factory=list)
    gate_result: GateResult | None = None
    recovery_budget_remaining: int | None = None
    override: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

The `__iter__` method is important. It lets this continue to work:

```python
producer_out, evaluator_out = run_eval_optimizer_loop(...)
```

That keeps existing unit tests and older callers mostly compatible while allowing `run.py` to consume the richer result.

---

## 5.2 `core/evaluator_output.py`

Create one parser and pass/fail interpreter for all evaluator outputs.

Responsibilities:

1. Extract JSON object from:

   * raw JSON
   * fenced JSON
   * fenced YAML
   * YAML mapping
   * surrounding prose with embedded JSON

2. Normalize result fields:

   * `overall_result`
   * `status`
   * `final_verdict.ready_for_release`
   * `programmatic_gates.all_gates_passed`
   * `issues[]`
   * `escalation_recommendation`
   * `failure_classification`

3. Replace `"PASS" in evaluator_out`.

Suggested public API:

```python
def parse_evaluator_output(raw: str) -> ParsedEvaluation:
    ...


def evaluator_passed(raw: str) -> bool:
    return parse_evaluator_output(raw).passed


def issue_fingerprint(parsed: ParsedEvaluation) -> str:
    ...
```

Pass detection should follow this order:

```python
def _compute_passed(parsed: dict | None, raw_text: str) -> bool:
    if parsed:
        overall = _lower(parsed.get("overall_result"))
        status = _lower(parsed.get("status"))
        gates = parsed.get("programmatic_gates") or {}
        final = parsed.get("final_verdict") or {}

        if gates.get("all_gates_passed") is False:
            return False

        if final.get("ready_for_release") is False:
            return False

        issues = parsed.get("issues") or []
        if any(_severity(issue) in {"critical", "high"} for issue in issues):
            return False

        if overall == "pass":
            return True

        if status == "pass":
            return True

        if status == "PASS".lower():
            return True

        return False

    # Legacy fallback only.
    # Accept "PASS" only when it appears as a leading verdict, not anywhere.
    return bool(re.search(r"(?im)^\s*PASS\b", raw_text or ""))
```

Examples that must be false:

```text
"Does not PASS"
"PASS criteria are not met"
"The previous PASS claim was incorrect"
'{"overall_result":"fail","notes":"Would pass after fixes"}'
```

Examples that must be true:

```text
"PASS"
"PASS - all gates satisfied"
'{"overall_result":"pass","issues":[]}'
```

---

## 5.3 `core/artifact_gates.py`

Move deterministic validation out of ad hoc scripts and into importable Python functions.

Suggested public API:

```python
def run_task_plan_gates(change_id: str, *, agent_context_root: Path) -> GateResult:
    ...

def run_assignment_gates(change_id: str, *, agent_context_root: Path) -> GateResult:
    ...

def run_impl_gates(change_id: str, uow_id: str, *, agent_context_root: Path) -> GateResult:
    ...

def run_qa_gates(change_id: str, *, agent_context_root: Path) -> GateResult:
    ...

def run_acceptance_gate(change_id: str, *, agent_context_root: Path) -> GateResult:
    ...
```

This should replace orchestration reliance on hardcoded paths like:

```python
agent-context/{change_id}/...
```

Use the runtime root from:

```python
core.runtime_paths.agent_context_root()
```

or pass the root in explicitly so tests can patch it.

### Task plan gates

Validate:

```text
planning/tasks.yaml exists
YAML parses
top-level mapping
tasks list exists and is non-empty
each task has id/title/ac_mapping/dependencies/priority/complexity where expected
all ACs from intake/story.yaml are covered
no unknown AC ids
task ids unique
dependencies reference existing tasks
dependency graph has no cycle
task count is sane
```

Use current task expectations from `task-generator/v2/prompt.md`:

```text
2-8 broad tasks typically
never fewer than 2 tasks
```

But implement the hard gate as configurable:

```python
min_tasks = 2
max_tasks = 15
```

### Assignment gates

Validate:

```text
planning/assignments.json exists
JSON/YAML parses via load_assignments_file
batches is a non-empty list
batch_id exists
uows are non-empty
uow_id unique
source_task_id references a task
every task is assigned exactly once
dependencies reference valid UoWs
dependencies are scheduled before dependent UoWs
parallel batches do not contain dependency conflicts
execution/<uow_id>/uow_spec.yaml exists for every UoW
uow_spec ac_mapping matches source task ac_mapping
```

### Implementation gates

Validate:

```text
execution/<uow_id>/uow_spec.yaml exists
execution/<uow_id>/impl_report.yaml exists
impl_report parses
impl_report aligns with current UoW
impl_report.status == complete
definition_of_done_status exists and all met == true
files_modified exists when applicable
test_results are present when the UoW required testing
```

Reuse existing logic:

```python
normalize_impl_report_file(...)
validate_impl_report_alignment(...)
```

### QA gates

First fix the QA schema mismatch.

The QA prompt currently asks for:

```yaml
story_id:
qa_status:
acceptance_criteria_validation:
issues_found:
final_recommendation:
```

The validator currently expects:

```yaml
change_id:
overall_status:
ac_validations:
```

Implement a normalizer:

```python
def normalize_qa_report(data: dict) -> dict:
    ...
```

It should accept both shapes and return a canonical internal shape:

```python
{
    "change_id": "...",
    "overall_status": "pass|fail|blocked|conditional_pass",
    "ac_validations": [
        {
            "ac_id": "AC1",
            "status": "pass|fail|partial|blocked",
            "evidence": [...],
            "source_schema": "qa_v2"
        }
    ],
    "issues": [...],
    "final_recommendation": "approve|reject|approve_with_conditions"
}
```

QA gates should validate:

```text
qa/qa_report.yaml exists
report parses
every AC in story.yaml has a validation entry
each validation has non-empty evidence
no AC validation has fail/blocked
no critical/high issue remains unresolved
final_recommendation is approve or approve_with_conditions with no blocking conditions
```

For the first implementation, treat `approve_with_conditions` as nonblocking only when conditions are low-severity documentation/reporting conditions. If conditions affect behavior, compatibility, or missing evidence, block.

---

## 5.4 `core/ac_ledger.py`

Add an acceptance criteria ledger.

This is the final arbiter.

Suggested public API:

```python
def build_ac_ledger(change_id: str, *, agent_context_root: Path) -> dict:
    ...

def write_ac_ledger(change_id: str, *, agent_context_root: Path) -> Path:
    ...

def evaluate_ac_ledger(ledger: dict) -> GateResult:
    ...
```

Write to:

```text
agent-context/<change-id>/summary/ac_ledger.yaml
agent-context/<change-id>/summary/acceptance_gate.yaml
```

Canonical ledger shape:

```yaml
change_id: TEST-123
updated_at: "2026-06-02T..."
acceptance_criteria:
  AC1:
    text: "..."
    planned_by:
      tasks:
        - T1
    assigned_to:
      uows:
        - UOW-001
    implemented_by:
      impl_reports:
        - execution/UOW-001/impl_report.yaml
      status: complete
      evidence:
        - "definition_of_done_status[0].evidence"
    verified_by:
      qa_report: qa/qa_report.yaml
      validation_status: pass
      evidence:
        - "qa/evidence/test_output/..."
    unresolved_issues: []
    status: verified
summary:
  ac_total: 3
  planned: 3
  assigned: 3
  implemented: 3
  verified: 3
  blocked: 0
  ready_for_release: true
```

A criterion is `verified` only when:

```text
planned_by is non-empty
assigned_to is non-empty
implemented_by has at least one complete implementation report
verified_by has QA validation status pass
verified_by has concrete evidence
there are no unresolved critical/high issues for that AC
```

The final gate should fail if any AC is not `verified`.

---

## 5.5 `core/orchestration_decisions.py`

Create durable decision logging.

Write append-only JSONL:

```text
agent-context/<change-id>/summary/orchestration_decisions.jsonl
```

Suggested public API:

```python
def append_decision(change_id: str, decision: LoopDecision, *, agent_context_root: Path) -> Path:
    ...

def read_decisions(change_id: str, *, agent_context_root: Path) -> list[dict]:
    ...
```

Each decision record should include:

```json
{
  "ts": "2026-06-02T...",
  "event": "loop_decision",
  "change_id": "TEST-123",
  "stage": "qa",
  "loop_name": "eval-optimizer",
  "uow_id": null,
  "actual_iterations": 3,
  "max_iterations": 3,
  "passed": false,
  "exhausted": true,
  "action": "create_bugfix_uow",
  "result": "recovery_started",
  "reason": "QA identified AC2 behavior failure",
  "issue_fingerprint": "sha256:...",
  "blocking_issue_count": 1,
  "nonblocking_issue_count": 0,
  "gate_passed": false,
  "recovery_budget_remaining": 2,
  "artifact_paths": [
    "qa/qa_report.yaml",
    "qa/eval_qa_3.json"
  ]
}
```

Because UoWs can execute in parallel, protect JSONL appends with a module-level `threading.Lock`.

---

## 5.6 `core/loop_exhaustion.py`

Implement the actual exhaustion handler.

Suggested public API:

```python
@dataclass
class WorkflowRecoveryPolicy:
    max_same_stage_repairs: int = 1
    max_qa_bugfix_cycles: int = 2
    max_total_recovery_actions: int = 5
    max_same_issue_repeats: int = 2
    allow_evidence_overrides: bool = True
    fail_on_unresolved_blocking_issues: bool = True


@dataclass
class WorkflowRecoveryState:
    total_recovery_actions_used: int = 0
    same_stage_repairs: dict[str, int] = field(default_factory=dict)
    qa_bugfix_cycles_used: int = 0
    issue_fingerprint_counts: dict[str, int] = field(default_factory=dict)


class LoopExhaustionHandler:
    def decide(
        self,
        result: LoopResult,
        *,
        policy: WorkflowRecoveryPolicy,
        state: WorkflowRecoveryState,
    ) -> LoopDecision:
        ...
```

Decision order:

```text
1. If result.passed:
     continue

2. If not result.exhausted and status is error:
     fail

3. Run deterministic gates for the stage.

4. Combine evaluator issues + gate issues.

5. If any true human-only blocker exists:
     human_escalation_required

6. If deterministic gates pass and no blocking issues:
     continue_with_evidence

7. If stage has deterministic repair available and budget remains:
     repair_same_stage

8. If QA found bug and bugfix budget remains:
     create_bugfix_uow

9. If issue belongs to earlier stage and rewind budget remains:
     route_to_upstream_stage

10. Else:
     fail_unresolved
```

---

# 6. Update `core/evaluator_optimizer_loops.py`

## 6.1 Add explicit stage parameters

Change `run_eval_optimizer_loop(...)` signature to include:

```python
def run_eval_optimizer_loop(
    producer_func,
    producer_input,
    evaluator_func,
    evaluator_prompt,
    iter_count: int = 3,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    evaluator_runner: str | None = None,
    evaluator_runner_model: str | None = None,
    stage: str = "eval-optimizer",
    artifact_kind: str | None = None,
    expected_artifact_paths: list[str] | None = None,
) -> LoopResult:
```

For backward compatibility, all new parameters should have defaults.

`run.py` should pass:

```python
stage="task-generation"
artifact_kind="tasks"
expected_artifact_paths=[str(AGENT_CONTEXT_ROOT / change_id / "planning" / "tasks.yaml")]
```

For assignment:

```python
stage="task-assignment"
artifact_kind="assignments"
expected_artifact_paths=[str(AGENT_CONTEXT_ROOT / change_id / "planning" / "assignments.json")]
```

For QA:

```python
stage="qa"
artifact_kind="qa_report"
expected_artifact_paths=[str(AGENT_CONTEXT_ROOT / change_id / "qa" / "qa_report.yaml")]
```

## 6.2 Change pass detection

Replace:

```python
passed = "PASS" in evaluator_out
```

with:

```python
parsed = parse_evaluator_output(evaluator_out)
passed = parsed.passed
```

## 6.3 Persist evaluator output for every generic loop

Currently only implementation evaluator feedback is persisted by `_persist_impl_evaluator_feedback`.

Add generic persistence:

```python
def _persist_evaluator_feedback(
    *,
    change_id: str,
    stage: str,
    artifact_kind: str | None,
    iteration: int,
    evaluator_out: str,
) -> Path:
    ...
```

Recommended paths:

```text
task-generation → planning/eval_tasks_<iteration>.json
task-assignment → planning/eval_assignments_<iteration>.json
qa → qa/eval_qa_<iteration>.json
fallback → summary/eval_<stage>_<iteration>.json
```

Always include raw fallback fields if parsing fails:

```json
{
  "artifact_evaluated": "unknown",
  "overall_result": "fail",
  "raw_response": "..."
}
```

## 6.4 Emit correct loop status

Current exhausted loops emit:

```python
status="ok"
```

Change to:

```python
status = "passed" if passed else "exhausted"
```

Event should include:

```python
stop_reason="passed" if passed else "max_iterations"
blocking_issue_count=len(parsed.blocking_issues)
nonblocking_issue_count=len(parsed.nonblocking_issues)
issue_fingerprint=...
evaluator_artifact_path=str(evaluator_feedback_path)
artifact_hashes=...
```

## 6.5 Add stagnation detection

Track per iteration:

```text
expected artifact hashes
issue fingerprint
parsed blocking issue count
```

If the same artifact hash and same issue fingerprint repeat, stop early:

```text
stop_reason="stagnation"
exhausted=True
```

This saves tokens and moves to the exhaustion handler sooner.

Simple policy:

```python
if iteration >= 2 and current_fingerprint == previous_fingerprint and current_hashes == previous_hashes:
    stop_reason = "stagnation"
    exhausted = True
    break
```

## 6.6 Return `LoopResult`

At the end of both loop functions, return `LoopResult`, not a raw tuple.

Because `LoopResult.__iter__` yields `(producer_output, evaluator_output)`, existing tuple unpacking still works.

---

# 7. Update `core/steps.py`

## 7.1 Replace evaluator pass checks

These functions currently use `"PASS" in result` for Opik feedback:

```python
step_task_gen_evaluator
step_assignment_evaluator
step_software_engineer_evaluator
step_qa_evaluator
```

Replace each with:

```python
from core.evaluator_output import parse_evaluator_output

parsed = parse_evaluator_output(result)
passed = parsed.passed
```

This keeps trace scores consistent with loop decisions.

## 7.2 Move deterministic repair helpers out of `steps.py`

The following private functions are useful outside `steps.py`:

```python
_write_task_plan_fallback
_write_assignments_fallback
_normalize_task_plan_artifact
_normalize_assignments_artifact
_materialize_uow_specs_from_assignments
```

Move or wrap them in a new module:

```text
core/artifact_repair.py
```

Suggested public names:

```python
normalize_task_plan_artifact(change_id: str, *, agent_context_root: Path) -> bool
write_task_plan_fallback(change_id: str, *, agent_context_root: Path) -> Path
normalize_assignments_artifact(change_id: str, *, agent_context_root: Path) -> bool
write_assignments_fallback(change_id: str, *, agent_context_root: Path) -> Path
materialize_uow_specs_from_assignments(change_id: str, *, agent_context_root: Path) -> int
```

Then `steps.py` can import them instead of owning orchestration repair logic.

## 7.3 Generalize single-task expansion

Current `_normalize_task_plan_artifact` expands one-task plans only for calibration IDs:

```python
def _should_expand_single_task_plan(change_id: str) -> bool:
    return change_id.startswith("calibration_")
```

Change this behavior.

If the task plan has one task and all ACs are covered, add a verification/alignment task for any workflow run, not only calibration runs.

Reason: the task-generator prompt explicitly says never produce fewer than two tasks.

---

# 8. Update `run.py`

This is the main orchestration change.

## 8.1 Import new orchestration utilities

Near the existing loop import:

```python
from core.evaluator_optimizer_loops import run_eval_optimizer_loop, run_uow_eval_loop
```

Add:

```python
from core.loop_exhaustion import (
    LoopExhaustionHandler,
    WorkflowRecoveryPolicy,
    WorkflowRecoveryState,
)
from core.artifact_gates import run_acceptance_gate
from core.ac_ledger import write_ac_ledger
from core.evaluation_contracts import LoopResult, LoopDecision
from core.workflow_errors import LoopExhaustedUnresolvedError, AcceptanceGateError
```

## 8.2 Create recovery state once per workflow

After `loop_iter_count`:

```python
loop_iter_count = 1 if calibration_fast_mode else 3

recovery_policy = WorkflowRecoveryPolicy.for_mode(
    calibration_fast_mode=calibration_fast_mode,
    headless=headless,
)
recovery_state = WorkflowRecoveryState()
exhaustion_handler = LoopExhaustionHandler(
    change_id=resolved_change_id,
    repo=resolved_repo,
    agent_context_root=AGENT_CONTEXT_ROOT,
    emit_event=_emit,
)
```

Recommended policy:

```python
normal:
  max_same_stage_repairs: 1
  max_qa_bugfix_cycles: 2
  max_total_recovery_actions: 5
  max_same_issue_repeats: 2

calibration_fast_mode:
  max_same_stage_repairs: 0
  max_qa_bugfix_cycles: 0
  max_total_recovery_actions: 1
  allow_evidence_overrides: true
```

## 8.3 Add a helper to handle loop results

In `run.py`, add:

```python
def _handle_loop_result_or_raise(
    *,
    result: LoopResult | object,
    handler: LoopExhaustionHandler,
    policy: WorkflowRecoveryPolicy,
    state: WorkflowRecoveryState,
) -> LoopDecision | None:
    if not isinstance(result, LoopResult):
        # Compatibility with existing tests that patch loop functions to return None.
        return None

    decision = handler.handle(result, policy=policy, state=state)

    if decision.action in {"continue", "continue_with_evidence"}:
        return decision

    if decision.action == "fail_unresolved":
        raise LoopExhaustedUnresolvedError.from_decision(decision)

    if decision.action == "human_escalation_required":
        raise LoopExhaustedUnresolvedError.from_decision(decision)

    return decision
```

The actual `handler.handle(...)` can both decide and apply simple repair actions, or it can return a decision that `run.py` applies. I recommend this split:

```text
handler.decide(...) chooses action
run.py applies action
handler.record_decision(...) records action/result
```

That keeps orchestration explicit.

## 8.4 Task-generation stage update

Current code:

```python
run_eval_optimizer_loop(...)
last_completed_stage = "task-generation"
failed_stage = None
_require_file(... "tasks.yaml")
```

Change to:

```python
task_result = run_eval_optimizer_loop(
    producer_func=steps.step_task_gen_producer,
    producer_input=task_gen_input,
    evaluator_func=steps.step_task_gen_evaluator,
    evaluator_prompt=task_gen_evaluator_prompt,
    iter_count=loop_iter_count,
    stage="task-generation",
    artifact_kind="tasks",
    expected_artifact_paths=[
        str(AGENT_CONTEXT_ROOT / resolved_change_id / "planning" / "tasks.yaml")
    ],
    **_agent_llm_kwargs(agent_llms, "task-generator"),
    evaluator_runner=agent_llms["task-plan-evaluator"]["runner"],
    evaluator_runner_model=agent_llms["task-plan-evaluator"]["model"],
)

decision = _handle_loop_result_or_raise(
    result=task_result,
    handler=exhaustion_handler,
    policy=recovery_policy,
    state=recovery_state,
)

_require_file(resolved_change_id, "task-generation", "planning", "tasks.yaml")
```

If the handler returns `repair_same_stage`, apply deterministic repair and re-run gates. For task generation, recovery does **not** need to rerun the full loop if deterministic repair succeeds.

## 8.5 Task-assignment stage update

Same structure, but pass:

```python
stage="task-assignment"
artifact_kind="assignments"
expected_artifact_paths=[
    str(AGENT_CONTEXT_ROOT / resolved_change_id / "planning" / "assignments.json")
]
```

After handling:

```python
_require_file(resolved_change_id, "task-assignment", "planning", "assignments.json")
```

Then run assignment gates explicitly.

## 8.6 Execution stage update

Current execution stage has nested `_run_uow_with_events(...)`.

Update `_run_uow_with_events(...)` so it captures the loop result:

```python
result = run_uow_eval_loop(...)

decision = _handle_loop_result_or_raise(
    result=result,
    handler=exhaustion_handler,
    policy=recovery_policy,
    state=recovery_state,
)
```

If a UoW exhausts with unresolved blocking issues, raise. That causes current batch execution to fail, which is correct.

For parallel batches, be careful:

```text
UoW-local repair is allowed in parallel.
Global planning/assignment/QA recovery is not allowed inside parallel UoW threads.
```

So UoW handler actions should be limited to:

```text
continue_with_evidence
repair_same_stage for that UoW
fail_unresolved
human_escalation_required
```

Do not create global bugfix UoWs from inside a parallel UoW thread.

## 8.7 QA stage update

After the QA loop, currently the workflow immediately marks QA complete.

Change to:

```python
qa_result = run_eval_optimizer_loop(
    producer_func=steps.step_qa_engineer,
    producer_input=qa_producer_input,
    evaluator_func=steps.step_qa_evaluator,
    evaluator_prompt=qa_evaluator_prompt,
    iter_count=loop_iter_count,
    stage="qa",
    artifact_kind="qa_report",
    expected_artifact_paths=[
        str(AGENT_CONTEXT_ROOT / resolved_change_id / "qa" / "qa_report.yaml")
    ],
    **_agent_llm_kwargs(agent_llms, "qa-engineer"),
    evaluator_runner=agent_llms["qa-evaluator"]["runner"],
    evaluator_runner_model=agent_llms["qa-evaluator"]["model"],
)

decision = _handle_loop_result_or_raise(
    result=qa_result,
    handler=exhaustion_handler,
    policy=recovery_policy,
    state=recovery_state,
)

_require_file(resolved_change_id, "qa", "qa", "qa_report.yaml")
```

Then write and check the AC ledger:

```python
write_ac_ledger(resolved_change_id, agent_context_root=AGENT_CONTEXT_ROOT)
final_gate = run_acceptance_gate(resolved_change_id, agent_context_root=AGENT_CONTEXT_ROOT)

if not final_gate.passed:
    raise AcceptanceGateError.from_gate(final_gate)
```

This final gate must happen before PR creation and before marking the workflow successful.

---

# 9. Implement stage-specific exhaustion behavior

## 9.1 Task-generation exhaustion policy

When task generation exhausts:

### Continue with evidence when

```text
tasks.yaml exists
task gates pass
all ACs are covered
no high/critical evaluator issues remain
```

Record:

```text
action=continue_with_evidence
override=true
reason="Evaluator did not return pass, but task-plan hard gates passed and remaining issues were nonblocking."
```

### Repair same stage when

```text
schema aliases are wrong
single-task plan needs verification task
AC coverage is missing
dependency graph has trivial issue
task count is too low/high but repairable
```

Repairs:

1. Normalize aliases:

   * `task_id` → `id`
   * `acceptance_criteria_mapped` → `ac_mapping`
   * `estimated_complexity` → `complexity`

2. If exactly one task exists and covers ACs:

   * Add `T2` verification/alignment task.
   * Depend on `T1`.
   * Copy AC mapping from `T1`.

3. If ACs are missing:

   * Add a task titled `Address missing acceptance criteria`.
   * Map only the missing ACs.
   * Depend on the last implementation task.

4. If dependencies reference unknown tasks:

   * Remove dependency only when it is clearly stale and not needed.
   * Otherwise fail unresolved.

5. If no task artifact exists:

   * Write deterministic fallback task plan using story ACs.
   * This currently exists as `_write_task_plan_fallback`; move/expose it.

After repair, rerun deterministic gates. Do not automatically assume success.

### Fail unresolved when

```text
story.yaml has no usable ACs
tasks.yaml remains malformed after repair
AC coverage cannot be established
evaluator reports a true human-only ambiguity
same issue fingerprint repeats beyond budget
```

---

## 9.2 Task-assignment exhaustion policy

Assignment is highly deterministic, so prefer deterministic repair.

### Continue with evidence when

```text
assignments.json exists
assignment gates pass
all tasks assigned exactly once
uow_spec.yaml exists for every UoW
no high/critical evaluator issues remain
```

### Repair same stage when

```text
assignments are malformed
parallel schedule is unsafe
some tasks are unassigned
uow_spec files are missing
dependencies are represented with task IDs instead of UoW IDs
```

Repairs:

1. Normalize assignments using existing parsing/normalization:

   * `parse_assignments_text`
   * `normalize_assignments_file`

2. If parallel safety is uncertain:

   * Set `parallel_execution=false`.

3. If assignment artifact is missing or irreparable:

   * Generate serial fallback schedule:

     * one UoW per task
     * one batch per UoW
     * dependencies mapped from task dependencies
   * This currently exists as `_write_assignments_fallback`; move/expose it.

4. Materialize UoW specs:

   * Use existing `_materialize_uow_specs_from_assignments`, moved/exposed.

After repair, rerun assignment gates.

### Fail unresolved when

```text
tasks.yaml is missing
task IDs cannot be mapped
dependency cycle exists upstream in tasks.yaml
assignment repair budget exhausted
```

---

## 9.3 UoW implementation exhaustion policy

UoW exhaustion is more serious than planning exhaustion but still not automatic failure.

### Continue with evidence when

```text
impl_report.yaml exists
impl gates pass
implementation evaluator failed only on low/medium reporting concerns
no critical/high issue remains
the UoW AC mappings are still covered elsewhere in the ledger
```

### Repair same stage when

```text
impl_report.yaml exists but has schema/reporting issues
definition_of_done_status is malformed but evidence is present
the evaluator asks for clearer evidence
the evaluator feedback is local to the UoW
```

Repair action:

Run one focused software engineer call, not the full loop:

```text
"Repair only implementation report/evidence for UoW X unless code changes are required.
Address these evaluator issues exactly.
Do not modify unrelated files."
```

Then rerun implementation gates and evaluator once.

### Rerun implementation when

```text
DoD item is not met
implementation report admits partial/blocked
test evidence shows failure
evaluator identifies a code issue local to this UoW
```

This should consume one same-stage recovery action.

### Fail unresolved when

```text
critical/high implementation issues remain
DoD remains unmet
impl_report is missing after recovery
alignment validation fails
same issue fingerprint repeats
```

Do not mark the UoW complete just because its report exists.

---

## 9.4 QA exhaustion policy

QA is the final quality stage, so treat it as release-critical.

### Continue with evidence when

```text
qa_report.yaml exists
QA gates pass
AC ledger says every AC is verified
no critical/high evaluator issues remain
```

This is the most important evidence-backed override case.

### Rerun QA when

```text
QA report is missing
QA report schema is wrong
evidence references are missing
some ACs lack QA validation
the implementation appears complete but QA evidence is weak
```

Use a focused QA prompt:

```text
"Repair qa_report.yaml and evidence references only.
Do not modify source code or tests.
Validate every AC from story.yaml.
Save raw command output under qa/evidence/..."
```

### Create bugfix UoW when

QA evaluator or QA report identifies a real code bug:

```text
failure_type == bug
issue severity critical/high
affected_ac is known
expected vs actual behavior is provided
```

Write:

```text
execution/BUGFIX-001/uow_spec.yaml
```

Suggested bugfix UoW shape:

```yaml
uow_id: BUGFIX-001
source_task_id: QA-BUGFIX-001
change_id: TEST-123
story_id: TEST-123
assigned_role: software-engineer
title: "Fix QA failure for AC2"
description: "QA identified behavior failure: ..."
ac_mapping:
  - AC2
dependencies: []
definition_of_done:
  - "Reproduce the QA failure from qa_report.yaml"
  - "Fix the underlying behavior"
  - "Run the relevant verification command"
  - "Update impl_report.yaml with evidence"
implementation_hints:
  - "qa/qa_report.yaml issues_found[0]"
  - "qa/evidence/test_output/..."
```

Then run:

```text
run_uow_eval_loop(BUGFIX-001)
rerun QA loop
rerun final acceptance gate
```

Budget:

```text
max_qa_bugfix_cycles = 2
```

### Human escalation required when

```text
failure_type == spec_ambiguity
failure_type == breaking_change requiring approval
evaluator escalation_recommendation.required == true
no safe default is inferable from story, constraints, or repository evidence
```

In headless/eval mode, do not ask the user repeatedly. Record the blocker and fail the workflow with a clear structured reason.

### Fail unresolved when

```text
QA still fails after bugfix budget
AC ledger still incomplete
critical/high QA issue remains
QA report is missing or malformed after recovery
```

---

# 10. Add workflow-level recovery loop around QA

The QA stage needs a bounded loop around the QA loop itself, not an infinite retry.

Pseudocode:

```python
qa_cycles = 0

while True:
    qa_result = run_qa_loop_once()
    decision = handler.decide(qa_result, policy=policy, state=state)

    if decision.action in {"continue", "continue_with_evidence"}:
        break

    if decision.action == "rerun_qa":
        if not state.consume_recovery("qa"):
            raise LoopExhaustedUnresolvedError.from_decision(decision)
        repair_qa_report_or_evidence(...)
        continue

    if decision.action == "create_bugfix_uow":
        if state.qa_bugfix_cycles_used >= policy.max_qa_bugfix_cycles:
            raise LoopExhaustedUnresolvedError.from_decision(decision)

        bugfix_ids = create_bugfix_uows_from_qa(...)
        for bugfix_id in bugfix_ids:
            run_uow_eval_loop(...)
        state.qa_bugfix_cycles_used += 1
        continue

    raise LoopExhaustedUnresolvedError.from_decision(decision)

write_ac_ledger(...)
acceptance_gate = run_acceptance_gate(...)
if not acceptance_gate.passed:
    raise AcceptanceGateError.from_gate(acceptance_gate)
```

This gives autonomy without an infinite loop.

---

# 11. Add final acceptance gate before PR

Before this block:

```python
with _Stage("pr-review"):
    ...
```

add:

```python
with _Stage("acceptance-gate"):
    failed_stage = "acceptance-gate"
    ledger_path = write_ac_ledger(
        resolved_change_id,
        agent_context_root=AGENT_CONTEXT_ROOT,
    )
    gate = run_acceptance_gate(
        resolved_change_id,
        agent_context_root=AGENT_CONTEXT_ROOT,
    )
    if not gate.passed:
        raise AcceptanceGateError.from_gate(gate)
    last_completed_stage = "acceptance-gate"
    failed_stage = None
```

Then update `_workflow_stage_names(...)` to include:

```python
"acceptance-gate"
```

before `pr-review`.

For evaluation runs, still run `acceptance-gate`; only skip PR review.

---

# 12. Update workflow status and error types

Add `core/workflow_errors.py`:

```python
class WorkflowOrchestrationError(RuntimeError):
    def __init__(self, message: str, *, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class LoopExhaustedUnresolvedError(WorkflowOrchestrationError):
    @classmethod
    def from_decision(cls, decision):
        return cls(
            f"{decision.stage} exhausted with unresolved blocking issues: {decision.reason}",
            metadata={...},
        )


class AcceptanceGateError(WorkflowOrchestrationError):
    @classmethod
    def from_gate(cls, gate):
        return cls(
            f"Acceptance gate failed: {len(gate.issues)} issue(s)",
            metadata={...},
        )
```

Update `run.py` `_serialize_exception(...)`:

```python
def _serialize_exception(exc: BaseException) -> dict:
    payload = {
        "exception_type": type(exc).__name__,
        "message": _summarize_exception(exc),
    }
    metadata = getattr(exc, "metadata", None)
    if isinstance(metadata, dict):
        payload["metadata"] = metadata
    return payload
```

This gives `workflow_status.yaml` useful failure context.

Do not add a new terminal job status like `blocked` in the first implementation. Existing server code treats terminal statuses as:

```python
{"succeeded", "failed", "cancelled"}
```

Use:

```text
status: failed
failure_category: human_blocker | unresolved_loop_exhaustion | acceptance_gate_failed
```

inside metadata/events for now.

Adding a real `blocked` terminal status can be a later UI/server migration.

---

# 13. Update telemetry and observability

## 13.1 Events to emit

Add these event types:

```text
loop.exhausted
loop.decision
recovery.start
recovery.end
acceptance_gate.start
acceptance_gate.end
```

Example:

```python
_emit(
    "loop.decision",
    stage=decision.stage,
    action=decision.action,
    result=decision.result,
    reason=decision.reason,
    override=decision.override,
    blocking_issue_count=len(decision.blocking_issues),
    nonblocking_issue_count=len(decision.nonblocking_issues),
)
```

## 13.2 Update `server/telemetry_analysis.py`

Extend `extract_loop_instances(...)` to include:

```python
"status": event.get("status"),
"stop_reason": event.get("stop_reason"),
"issue_fingerprint": event.get("issue_fingerprint"),
"blocking_issue_count": event.get("blocking_issue_count"),
"nonblocking_issue_count": event.get("nonblocking_issue_count"),
```

Add a new extractor:

```python
def extract_loop_decisions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ...
```

Include in profile:

```python
"loop_decisions": loop_decisions,
"loop_exhaustion_count": ...,
"evidence_override_count": ...,
"recovery_action_count": ...,
"acceptance_gate": ...
```

## 13.3 Update `run.py` run metrics

`_write_run_metrics(...)` should summarize:

```text
loop_exhaustions_by_stage
loop_decisions_by_action
recovery_actions_by_stage
evidence_overrides
acceptance_gate_status
ac_total
ac_verified
ac_blocked
```

This supports the user’s goal of evaluating workflow changes over time.

---

# 14. Reconcile artifact schema validators

There are two schema validator scripts:

```text
agent-script-source/validate-artifact-schema.py
scripts/validate-artifact-schema.py
```

They are inconsistent.

Implementation plan:

1. Move shared schema validation into:

```text
core/artifact_schemas.py
```

2. Make both scripts thin wrappers around that module.

3. Support all artifact types in both wrappers:

```text
tasks
assignments
impl_report
qa_report
```

4. Add QA schema compatibility:

   * accept current QA prompt schema
   * accept legacy validator schema
   * normalize internally

5. Keep script CLI stable:

```bash
python3 scripts/validate-artifact-schema.py --type qa_report path/to/qa_report.yaml
```

6. Update agent prompts only after code supports both shapes.

This prevents evaluator loops from exhausting because the QA producer and QA schema validator disagree.

---

# 15. Update agent prompts

The code should not depend on prompt changes alone, but the prompts should be aligned.

Update latest versions under:

```text
agent-definition-source/task-plan-evaluator/v2/prompt.md
agent-definition-source/assignment-evaluator/v2/prompt.md
agent-definition-source/implementation-evaluator/v3/prompt.md
agent-definition-source/qa-evaluator/v2/prompt.md
agent-definition-source/qa/v2/prompt.md
```

## 15.1 Evaluator prompts

Require a clear machine-readable verdict:

```text
Your final response must be a single JSON object.
Do not wrap it in prose.
Set overall_result to "pass" or "fail".
Set programmatic_gates.all_gates_passed.
Include issues[].
Use severity critical/high/medium/low.
```

## 15.2 QA prompt

Align QA report schema with the validator.

Either choose the current QA prompt shape as canonical:

```yaml
story_id:
qa_status:
acceptance_criteria_validation:
issues_found:
final_recommendation:
```

or choose the validator shape:

```yaml
change_id:
overall_status:
ac_validations:
```

Recommended: keep the richer QA prompt shape as canonical and make validators normalize both.

## 15.3 Materialization reminder

This repo does not materialize prompts automatically during normal runs. After prompt updates, operators must run:

```bash
python3 core/materialize.py
```

or run the workflow with:

```bash
python3 run.py --materialize ...
```

The code changes must work even if prompts are not yet materialized.

---

# 16. Evaluation and testing plan

## 16.1 Unit tests for evaluator parsing

Add `tests/test_evaluator_output.py`.

Cases:

```python
PASS -> passed True
PASS - all good -> passed True
Does not PASS -> passed False
PASS criteria are not met -> passed False
{"overall_result":"pass"} -> passed True
{"overall_result":"fail","issues":[]} -> passed False
{"overall_result":"pass","programmatic_gates":{"all_gates_passed":false}} -> passed False
{"overall_result":"pass","issues":[{"severity":"critical"}]} -> passed False
fenced JSON -> parsed
fenced YAML -> parsed
malformed response -> parsed None and passed False
```

## 16.2 Unit tests for loop results

Extend `tests/test_evaluator_optimizer_loops.py`.

Add tests:

```text
exhausted loop returns LoopResult
LoopResult can still be tuple-unpacked
loop.end status is exhausted when max iterations reached
loop.end includes stop_reason=max_iterations
"does not PASS" does not pass
generic evaluator output is persisted to planning/eval_tasks_1.json
uow evaluator output still persists to eval_impl_1.json
stagnation stops before full iteration count when same issue/artifact repeats
```

Existing tests should still pass because of `LoopResult.__iter__`.

## 16.3 Unit tests for artifact gates

Add `tests/test_artifact_gates.py`.

Task gates:

```text
valid tasks pass
missing AC fails
dependency cycle fails
legacy aliases fail before normalization and pass after repair
single task fails task-count gate before repair and passes after repair
```

Assignment gates:

```text
valid assignments pass
unassigned task fails
duplicate UoW fails
unsafe parallel dependency fails
serial fallback passes
uow_spec materialization works
```

Implementation gates:

```text
valid impl report passes
missing impl report fails
mismatched uow_id fails
DoD met=false fails
status partial fails
```

QA gates:

```text
current qa/v2 schema passes after normalization
legacy qa schema passes after normalization
missing AC validation fails
empty evidence fails
critical issue fails
approve_with_conditions with blocking condition fails
```

## 16.4 Unit tests for exhaustion handler

Add `tests/test_loop_exhaustion.py`.

Cases:

```text
passed result -> continue
exhausted + gates pass + no blocking issues -> continue_with_evidence
exhausted + task schema issue -> repair_same_stage
exhausted + assignment missing artifact -> repair_same_stage
exhausted + QA bug -> create_bugfix_uow
exhausted + spec ambiguity -> human_escalation_required
same issue fingerprint repeated beyond budget -> fail_unresolved
```

## 16.5 Unit tests for AC ledger

Add `tests/test_ac_ledger.py`.

Cases:

```text
all ACs planned/assigned/implemented/verified -> ready_for_release true
missing task mapping -> false
missing UoW -> false
impl report partial -> false
QA missing evidence -> false
unresolved critical issue -> false
```

## 16.6 Run main orchestration tests

Update existing `tests/test_run_main_model_override.py`.

Important compatibility issue: many tests patch loop functions to return `None`.

`run.py` should tolerate non-`LoopResult` in tests/legacy mocks.

Add tests:

```text
task-generation exhausted with blocking gates raises
task-generation exhausted but gates pass continues and records decision
qa_report.yaml is required after QA
acceptance-gate runs before pr-review
evaluation runs skip pr-review but still run acceptance-gate
```

## 16.7 Telemetry tests

Add tests to assert:

```text
loop.exhausted event appears
loop.decision event appears
telemetry profile includes loop_exhaustion_count
run_metrics.yaml includes loop decision summary
```

## 16.8 Full local test command

Use:

```bash
python3 -m unittest discover -s tests
```

For focused iteration:

```bash
python3 -m unittest tests.test_evaluator_output
python3 -m unittest tests.test_evaluator_optimizer_loops
python3 -m unittest tests.test_artifact_gates
python3 -m unittest tests.test_loop_exhaustion
python3 -m unittest tests.test_ac_ledger
```

## 16.9 Workflow evals

After unit tests pass, run agent-level evals:

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset "$AGENT_RUNNER_DATA_DIR/eval/agent_datasets/task-generator/smoke.jsonl" \
  --dry-run
```

Then run workflow benchmark:

```bash
python3 eval/runner.py --difficulty easy --runs 3
```

Then:

```bash
python3 eval/runner.py --difficulty easy medium --runs 3 --compare-to "$AGENT_RUNNER_DATA_DIR/eval/reports/baseline.json"
```

The report should include new observability:

```text
loop exhaustion count
recovery action count
evidence override count
AC ledger pass/fail
tokens per successful AC
```

---

# 17. Rollout plan

## Phase 1: Observe without changing success/failure behavior

Add:

```text
LoopResult
safe evaluator parser
generic evaluator persistence
loop.end status=exhausted
orchestration decision logging
deterministic gates
```

But run in “observe” mode:

```text
record what would have happened
do not yet block production workflows
```

Environment flag:

```bash
AGENT_RUNNER_EXHAUSTION_POLICY=observe
```

This should be temporary.

## Phase 2: Enforce for QA and final acceptance gate

Make these blocking:

```text
qa_report.yaml required
acceptance gate required before PR
unresolved critical/high QA issue blocks PR
```

This directly protects the North Star.

## Phase 3: Enforce for planning and assignment

Block or repair exhausted planning/assignment loops.

This will likely expose existing schema mismatch and planner quality issues, so do it after schema validators are reconciled.

## Phase 4: Add automated QA bugfix cycles

Add:

```text
QA bug → create bugfix UoW → execute → rerun QA
```

This is the highest-leverage autonomy improvement, but it is also the most complex because it rewinds from QA back into execution.

## Phase 5: Add UI summaries

Add visibility in the GUI:

```text
loop exhausted badges
recovery action timeline
AC ledger summary
evidence override count
```

---

# 18. Recommended first PR boundary

The first implementation PR should not try to do everything.

Recommended first PR scope:

```text
1. Add LoopResult.
2. Add evaluator output parser.
3. Replace "PASS" substring checks.
4. Persist evaluator outputs for generic loops.
5. Emit exhausted loop status correctly.
6. Add deterministic gates.
7. Require qa_report.yaml.
8. Add final AC ledger and acceptance gate.
9. Record orchestration decisions.
10. Fail on unresolved exhausted QA/acceptance-gate blockers.
```

Leave full QA bugfix UoW creation for the second PR.

Reason: the first PR eliminates silent success and adds the final acceptance gate. That directly addresses the most dangerous current behavior while keeping the implementation manageable.

---

# 19. Concrete acceptance criteria for this implementation

The implementation is complete when these are true:

1. An exhausted loop no longer emits `status="ok"`.

2. `run_eval_optimizer_loop(...)` and `run_uow_eval_loop(...)` return a structured `LoopResult`.

3. Existing tuple unpacking still works:

```python
producer_out, evaluator_out = run_eval_optimizer_loop(...)
```

4. `"Does not PASS"` is never treated as success.

5. Generic evaluator outputs are persisted under stage-appropriate paths.

6. `run.py` does not continue silently after an exhausted loop.

7. Every exhausted loop produces a durable decision record in:

```text
agent-context/<change-id>/summary/orchestration_decisions.jsonl
```

8. Task generation exhaustion can continue only if task gates pass or deterministic repair succeeds.

9. Task assignment exhaustion can continue only if assignment gates pass or deterministic repair succeeds.

10. UoW exhaustion can continue only if implementation gates pass and no blocking issues remain.

11. QA exhaustion can continue only if QA gates and the AC ledger pass.

12. `qa/qa_report.yaml` is required before QA is considered complete.

13. `summary/ac_ledger.yaml` is written before PR review.

14. PR review cannot run unless the final acceptance gate passes.

15. Evaluation runs still skip PR review, but they do not skip the acceptance gate.

16. Workflow status includes structured failure metadata for unresolved exhaustion or acceptance-gate failure.

17. Telemetry exposes:

* loop exhaustion count
* recovery decisions
* evidence overrides
* final AC gate status

18. Unit tests cover parser, gates, handler decisions, ledger, and run orchestration.

19. Workflow eval reports can compare quality and token usage before/after the change.

---

# 20. Final recommended behavior in one sentence

When a loop reaches max iterations, **stop that loop, mark it exhausted, run deterministic evidence checks and an automated recovery decision, continue only with a recorded evidence-backed pass/override, and always require the final acceptance ledger to pass before completion or PR creation.**
