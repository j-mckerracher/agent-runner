# Rule Recommendations from TEST-AC-001 Lessons Optimization

**Date**: 2026-04-22  
**Source**: TEST-AC-001 synthetic workflow smoke test  
**Overall Confidence**: 92/100

## Summary

Five prevention rules were drafted from the TEST-AC-001 workflow to prevent recurrence of test assertion mismatches, implementation report accuracy issues, and documentation/implementation alignment problems.

**Status**: 
- ✅ **RULE-001** (High confidence): Injected into software-engineer-hyperagent.agent.md
- 📋 **RULE-002 through RULE-005** (Medium-High confidence): Recommended for review and injection

---

## Injected Rules (Already Applied)

### RULE-001: Test Assertion Schema Verification
**Target Agent**: software-engineer-hyperagent  
**Confidence**: 95/100  
**Status**: ✅ Injected  

**Rule Summary**: Before writing test assertions to verify artifact structure, ALWAYS inspect actual artifacts first using grep/YAML parsing. DO NOT write tests expecting fields without confirming they exist.

**Prevents**: TEST-001 class failures (10 test failures in TEST-AC-001)

---

## Recommended Rules (Pending Review & Injection)

### RULE-002: Implementation Report Test Pass Rate Accuracy
**Target Agent**: software-engineer-hyperagent  
**Confidence**: 95/100  
**Priority**: High  
**Status**: 📋 Recommended  

**Rule Summary**: When writing impl_report.yaml, include actual pytest output showing exact pass/fail counts. DO NOT claim "all tests pass" without evidence showing the pytest summary line with counts.

**Rationale**: UOW-003 and UOW-004 claimed 100% pass rates, but actual execution showed failures.

---

### RULE-003: Documentation Schema Alignment Verification
**Target Agent**: software-engineer-hyperagent  
**Confidence**: 90/100  
**Priority**: High  
**Status**: 📋 Recommended  

**Rule Summary**: When writing documentation that references artifact fields or markers, verify those fields exist in actual artifacts using grep/YAML inspection BEFORE finalizing documentation.

**Rationale**: README.md documented project_type='synthetic-fixture' as marker, but actual config.yaml has run_metadata.source_type instead.

---

### RULE-004: Artifact Schema Specification Requirement
**Target Agent**: task-generator  
**Confidence**: 85/100  
**Priority**: High  
**Status**: 📋 Recommended  

**Rule Summary**: When creating artifacts consumed by downstream stages, document the exact schema including field names, locations, and expected values for downstream detection.

**Rationale**: Root cause of both TEST-001 and TEST-002. Intake agent created artifacts with non-obvious schema structure without documentation.

---

### RULE-005: Test Assertion Failure Analysis
**Target Agent**: qa-evaluator  
**Confidence**: 95/100  
**Priority**: High  
**Status**: 📋 Recommended  

**Rule Summary**: When evaluating impl_report.yaml, run the full test suite independently and compare actual results vs reported claims. Flag discrepancies as high-severity issues.

**Rationale**: QA detected mismatch between claimed and actual pass rates only by running tests independently.

---

## Implementation Priority

### Immediate
- ✅ RULE-001: Already injected
- 📋 RULE-002: Inject into software-engineer-hyperagent
- 📋 RULE-005: Inject into qa-evaluator

### Near-term
- 📋 RULE-003: Inject into software-engineer-hyperagent
- 📋 RULE-004: Inject into task-generator

---

**Prepared by**: Lessons Optimizer Hyperagent  
**Confidence Score**: 92/100  
**Recommendation**: Apply all 5 rules to improve workflow robustness

---

# Rule Recommendations from Change 5035632 Lessons Optimization

**Date**: 2026-04-30  
**Source**: Change 5035632 dirty-state navigation guard (mcs-products-mono-ui, 3 UoWs)  
**Overall Confidence**: 90/100

## Summary

Four prevention rules drafted from change 5035632 execution. Two were high-confidence and injected directly into `04-software-engineer-hyperagent.agent.md`. Two require injection into agents that lack an `EVOLVING PROBLEM-SOLVING PIPELINES` block — recommended here for manual review.

**Status**:
- ✅ **RULE-SE-001** (High confidence): Injected into `04-software-engineer-hyperagent.agent.md` — self-verifying impl_report evidence
- ✅ **RULE-SE-002** (High confidence): Injected into `04-software-engineer-hyperagent.agent.md` — PrimeNG CoreHeader Cypress setup
- 📋 **RULE-5035632-REC-001** (High confidence): Recommended for task-assigner — invocation path validation
- 📋 **RULE-5035632-REC-002** (Medium confidence): Recommended for software-engineer — Nx project name discovery

---

## Injected Rules (Already Applied)

### RULE-SE-001: Self-Verifying impl_report Evidence
**Target Agent**: software-engineer-hyperagent  
**Confidence**: 92/100  
**Status**: ✅ Injected into `04-software-engineer-hyperagent.agent.md`  
**Source Signatures**: SIG-5035632-001, SIG-TEST-001 (cross-session repeat)

**Rule Summary**: For every DoD item verifying source code, embed VERBATIM code snippets in the `evidence` field. For all gate results include exact counts, exit code, and wall-clock timestamp for the current attempt. No vague confirmation phrases without actual content.

**Rationale**: REPEAT pattern (also seen in TEST-MEDIUM-001). UOW-001 required 3 evaluator attempts; UOW-003 required 3 evaluator attempts. Root cause in both: impl_report evidence too vague to self-verify without filesystem access. Cost: 4 wasted evaluator iterations across change 5035632.

---

### RULE-SE-002: PrimeNG CoreHeader Cypress Component Test Setup
**Target Agent**: software-engineer-hyperagent  
**Confidence**: 88/100  
**Status**: ✅ Injected into `04-software-engineer-hyperagent.agent.md`  
**Source Signatures**: SIG-5035632-003

**Rule Summary**: When mounting AppComponent or CoreHeader in Cypress: always use `provideNoopAnimations()` (suppresses NG05105), `createMockConfigStore('dev')` from `@rls/core/testing` instead of `new ConfigStore()` (prevents NG04002), and `.as('alias')` + `cy.get('@alias')` for stub assertions.

**Rationale**: Both NG05105 and NG04002 are triggered by standard Cypress mount setup omitting required providers. Required mid-implementation debugging in UOW-003.

---

## Recommended Rules (Pending Review & Injection)

### RULE-5035632-REC-001: Invocation Path Tilde Validation
**Target Agent**: task-assigner (03-task-assigner.agent.md)  
**Confidence**: 92/100  
**Priority**: Critical  
**Status**: 📋 Recommended — agent lacks EVOLVING PROBLEM-SOLVING block  

**Rule Summary**: When constructing evaluation invocation payloads (assignments.json, UoW invocations), validate that the `code_repo` path is a fully-expanded absolute path with no `~` character anywhere. Pattern to reject: any path matching `*/agent-runner/~/Code/*`. Correct form: `/Users/{username}/Code/{repo-name}`.

**Rationale**: The evaluator received `agent-runner/~/Code/mcs-products-mono-ui` as the repo path in both UOW-003 evaluation attempts. The evaluator halted immediately with `ARTIFACT ACCESS FAILURE` — no rubric items assessed. Cost: 2 completely wasted evaluator iterations for UOW-003. The path likely originated in the assignments.json or workflow invocation config.

**Trigger Check**: Before writing any evaluation invocation, run: `grep -o '~/Code' assignments.json` — if any match, expand to absolute path.

**Source Lessons**: SIG-5035632-002

---

### RULE-5035632-REC-002: Nx Project Name Discovery Before CLI Commands
**Target Agent**: software-engineer-hyperagent (supplemental — medium confidence)  
**Confidence**: 75/100  
**Priority**: Medium  
**Status**: 📋 Recommended  

**Rule Summary**: Before running `npx nx test <name>`, `npx nx build <name>`, or any Nx CLI command on a `libs/*` library, verify the Nx project name by reading `libs/{path-to-lib}/project.json` and checking the `name` field. Do not derive the project name from the directory path (e.g., `libs/shared/core` → project name is `core`, not `shared-core`).

**Rationale**: UOW-002 tool anomaly: `npx nx test shared-core` failed with "project not found". Actual name discovered as `core` by reading `project.json`. Resolved in-session within attempt 1, so low cost, but preventable.

**Trigger Check**: For any nx CLI command on a libs/* target: `cat libs/{path}/project.json | grep '"name"'` before running.

**Source Lessons**: SIG-5035632-004

---

## Implementation Priority

### Immediate
- ✅ RULE-SE-001: Already injected
- ✅ RULE-SE-002: Already injected
- 📋 RULE-5035632-REC-001: Add EVOLVING PROBLEM-SOLVING block to `03-task-assigner.agent.md` then inject, or add as explicit checklist item in the agent's Scope/Workflow section

### Near-term
- 📋 RULE-5035632-REC-002: Already partially addressed by RULE-SE-001; add explicit project.json lookup checklist

---

**Prepared by**: Lessons Optimizer Hyperagent  
**Run ID**: 5035632-lessons-optimizer-001  
**Confidence Score**: 90/100
