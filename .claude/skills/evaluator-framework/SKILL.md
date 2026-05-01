---
name: evaluator-framework
description: |
  Evaluator gate and rubric framework for AI agent quality checks. Use this skill when building or running evaluator agents that assess artifacts against programmatic gates and subjective rubrics.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is a rubric and gating reference, so only task-specific evaluator runs need live context.

# Evaluator Gate & Rubric Framework

Standard evaluation workflow for assessing artifacts in multi-agent workflows. All evaluator agents follow this framework.

## When to Use This Skill

Activate this skill when:

- Building an evaluator agent that assesses work artifacts
- Running programmatic gates before subjective assessment
- Applying rubric-based scoring to agent outputs
- Generating actionable feedback for artifact improvements
- Determining pass/fail for a workflow quality gate

## Evaluation Workflow (Sequential)

### Step 1: Run Programmatic Gates First

Deterministic checks run BEFORE any subjective assessment:

1. **Schema validation**: Artifact structure matches the expected schema
2. **Completeness checks**: All required elements are present
3. **Structural integrity**: Dependencies are valid, no cycles, no duplicates
4. **Range checks**: Counts and values within expected bounds

### Step 2: Gate-First Decision

```
Run all programmatic gates
  → If ANY gate fails → Set overall_result to FAIL
    → Populate rubric_results with gate-failure context
    → Skip full subjective rubric analysis
    → Proceed directly to output
  → If ALL gates pass → Proceed to rubric evaluation
```

**Important**: Gate failures are immediate FAIL — no subjective review is needed.

### Step 3: Rubric Evaluation

Score the artifact on defined dimensions. Each dimension has a severity:

| Severity      | Impact on Pass/Fail                          |
| ------------- | -------------------------------------------- |
| **Critical**  | Failure = overall FAIL                       |
| **Important** | Failure = warn; multiple failures may = FAIL |

Each dimension produces a result:

| Result | Meaning                                    |
| ------ | ------------------------------------------ |
| `pass` | Meets or exceeds standards                 |
| `warn` | Minor issues; acceptable but could improve |
| `fail` | Significant issues requiring remediation   |

### Step 4: Pass/Fail Decision Logic

- **PASS**: All critical checks pass AND no critical or high-severity issues exist
- **FAIL**: Any critical check fails OR any critical/high-severity issue exists

## Actionable Feedback Requirements

**Every issue MUST include:**

1. **Specific location**: Reference exact IDs (task IDs, AC numbers, UoW IDs, batch numbers, file paths)
2. **Clear description**: What is wrong or missing — not just a restatement of the rubric
3. **Concrete fix**: Actionable instruction to resolve the issue (e.g., "Move UOW-003 to batch 2 before UOW-004", "Add AC3 mapping to task T2")
4. **Expected outcome**: What the artifact should look like after the fix

### Anti-Patterns

❌ **Vague**: "Dependencies could be improved"
✅ **Actionable**: "UOW-003 is scheduled in batch 1 but depends on UOW-001 which is in batch 2. Move UOW-001 to batch 1 or UOW-003 to batch 3."

❌ **Restating the problem**: "AC coverage is incomplete"
✅ **Actionable**: "AC4 and AC5 are not mapped to any task. Add them to task T3 (which handles the related UI component) or create a new task T6 for these criteria."

## Standard Output Schema

Every evaluator output must include these fields:

```yaml
evaluation_id: '<unique identifier>'
artifact_evaluated: '<artifact filename>'
attempt_number: <integer>
overall_result: 'pass|fail'
score: <numeric score>

programmatic_gates:
  # Gate-specific boolean fields
  all_gates_passed: true|false

rubric_results:
  # Dimension-specific results
  <dimension_name>:
    result: 'pass|warn|fail'
    details: '<specific findings>'
    # Dimension-specific detail fields

issues:
  - issue_id: '<unique>'
    severity: 'critical|high|medium|low'
    category: '<evaluation category>'
    description: '<what is wrong>'
    location: '<specific ID or path>'
    actionable_fix: '<concrete fix instruction>'

actionable_fixes_summary:
  - '<ordered list of concise fix instructions>'

escalation_recommendation:
  required: true|false
  reason: '<reason or null>'

notes: '<additional observations>'
```

## Revision Handling

When evaluating a revision (attempt > 1):

1. Check if previous feedback was addressed
2. Verify that working elements from previous attempts were preserved
3. Focus evaluation on the areas that were flagged for revision
4. Note any new issues introduced during revision

---

## Programmatic Gate Scripts

The following scripts automate programmatic gates described in Step 1 above. Run these **before** rubric evaluation:

### Schema Validation Gate

```bash
~/.github/scripts/validate-artifact-schema.py --type <tasks|assignments|impl_report|qa_report> <artifact_path>
```

Validates artifact structure against the expected schema for its type. Reports all violations as a JSON issues array.

### Dependency Cycle Detection Gate

```bash
~/.github/scripts/check-dependency-cycles.py <tasks.yaml|assignments.json> [--type tasks|assignments]
```

Builds a DAG from dependency edges, runs topological sort, reports any cycles or dangling references.

### AC Coverage Completeness Gate

```bash
~/.github/scripts/check-ac-coverage.py <story.yaml> <tasks.yaml>
```

Verifies every acceptance criterion maps to at least one task. Reports unmapped ACs and tasks with no AC mapping.

### Test Harness Existence Gate

```bash
~/.github/scripts/check-test-harnesses.py <file1.component.ts> [file2.component.ts...]
```

Verifies modified Angular components have corresponding `*.test-harness.ts` files.

**All gate scripts**: Exit 0 = pass, exit 1 = fail, exit 2 = usage error. Output JSON to stdout.
