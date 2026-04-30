# Downstream Synthetic Mode Verification (UOW-004)

## Summary

This UoW verifies that downstream workflow stages (task-gen, task-assigner, software-engineer, QA) correctly detect synthetic mode and skip ADO operations. The verification focuses on the detection infrastructure that enables this capability.

## Synthetic Mode Detection Markers

Downstream stages detect synthetic mode using two mechanisms:

### Primary Marker: `config.yaml` Project Type
```yaml
# agent-context/TEST-AC-001/intake/config.yaml
change_id: TEST-AC-001
project_type: synthetic-fixture  # ← Primary marker for synthetic mode
code_repo: /Users/mckerracher.joshua/Code/Mine/agent-runner
```

**Detection Logic for Downstream Stages:**
```python
# Pseudocode that agents should implement
config = load_yaml("agent-context/[CHANGE-ID]/intake/config.yaml")
if config.get("project_type") == "synthetic-fixture":
    # Skip all ADO-specific operations
    skip_ado_operations = True
else:
    # Proceed with normal ADO workflow
    skip_ado_operations = False
```

### Secondary Marker: Absence of `ado_provenance`
```yaml
# agent-context/TEST-AC-001/intake/story.yaml
change_id: TEST-AC-001
title: "Synthetic workflow smoke test story"
description: "..."
acceptance_criteria:
  AC1: "..."
  AC2: "..."
  AC3: "..."
# ado_provenance field is ABSENT in synthetic mode
# (present in ADO mode for work-item tracking)
```

**Detection Logic:**
```python
story = load_yaml("agent-context/[CHANGE-ID]/intake/story.yaml")
if story.get("ado_provenance") is None:
    # Likely synthetic mode (verify with config.project_type)
    synthetic_mode_confirmed = (config.get("project_type") == "synthetic-fixture")
```

## Verification Tests

Created `tests/test_downstream_synthetic_mode.py` with 16 tests organized into 6 test classes:

### ConfigSyntheticModeDetectionTests (4 tests)
- ✅ Verify config.yaml has `project_type` field
- ✅ Verify `project_type` is set to `'synthetic-fixture'`
- ✅ Verify `ado_provenance` is absent in story.yaml
- ✅ Verify `change_id` matches between config and story

### TaskGeneratorSyntheticModeTests (2 tests)
- ✅ Verify task-gen receives config context
- ✅ Verify task-gen can detect synthetic from config

### AssignmentsJsonSyntheticHandlingTests (3 tests)
- ✅ Verify assignments.json was created
- ✅ Verify execution_schedule is defined
- ✅ Verify no ADO metadata references in assignments

### DownstreamSyntheticModeSkipLogicTests (2 tests)
- ✅ Verify synthetic marker enables skip logic
- ✅ Verify synthetic mode marker is unambiguous

### DownstreamPromptContextTests (3 tests)
- ✅ Verify task-gen prompt can reference config
- ✅ Verify story absence of ado_provenance signals synthetic
- ✅ Verify constraints.md documents synthetic handling

### SyntheticModeErrorHandlingTests (1 test)
- ✅ Verify synthetic mode requires no ADO calls

### IntegrationTestPlaceholder (1 test)
- ⏭️ Skipped: Integration test pending UOW-005 implementation

## Test Results

```
======================== 15 passed, 1 skipped in 1.08s =========================
```

All core synthetic mode detection tests pass. The integration test (full workflow verification) is deferred to UOW-005.

## Downstream Stage ADO Operation Skipping

Each downstream stage should implement the following logic:

### Task Generator
```
IF config.project_type == "synthetic-fixture" THEN
  - Read story/tasks normally
  - Generate tasks.yaml without ADO-specific metadata
  - Do NOT invoke azure-devops-cli
  - Do NOT write work items to ADO
ELSE
  - Proceed with normal ADO workflow
```

### Task Assigner
```
IF config.project_type == "synthetic-fixture" THEN
  - Read tasks.yaml and generate assignments.json
  - Do NOT write assignments back to ADO
  - Do NOT create/update ADO work items
  - Do NOT use ADO PAT or authentication
ELSE
  - Proceed with ADO assignment workflow
```

### Software Engineer
```
IF config.project_type == "synthetic-fixture" THEN
  - Implement UoW normally
  - Do NOT attempt to update ADO work-item status
  - Do NOT call azure-devops-cli skill
  - Generate impl_report.yaml locally only
ELSE
  - Proceed with ADO status updates
  - Call azure-devops-cli to update work-item state
```

### QA Engineer
```
IF config.project_type == "synthetic-fixture" THEN
  - Perform QA validation normally
  - Do NOT require Azure AD authentication
  - Do NOT attempt ADO connectivity checks
  - Generate qa_report.yaml locally only
ELSE
  - Proceed with ADO-integrated QA workflow
```

## Critical Path Dependencies

- **UOW-003 (T3)** ← Prerequisite: Creates intake artifacts with synthetic markers
- **UOW-004 (T4)** ← Current: Verifies downstream stage detection infrastructure
- **UOW-005 (T5)** ← Dependent: Integration test verifies end-to-end workflow

## Next Steps (UOW-005)

The integration test in UOW-005 will:
1. Run the full workflow with synthetic_story.json
2. Monitor for any azure-devops-cli calls (should be zero)
3. Verify all stages complete successfully
4. Confirm artifacts are created at each stage
5. Validate no external ADO API calls are made

## Implementation Notes

### Why Verification-Based Approach?
- Downstream agents (task-gen, task-assigner, software-engineer, QA) are agent definitions, not Python code in this repo
- The synthetic mode detection logic must be implemented in agent prompts/instructions
- This UoW verifies the infrastructure that enables detection (markers, artifact structure)
- The actual skip logic implementation is the responsibility of each agent's prompt

### Artifact Location Conventions
All synthetic mode detection depends on agents reading from standard artifact paths:
- `agent-context/[CHANGE-ID]/intake/config.yaml` ← project_type detection
- `agent-context/[CHANGE-ID]/intake/story.yaml` ← ado_provenance absence check
- `agent-context/[CHANGE-ID]/intake/constraints.md` ← synthetic nature documentation

### Detection Robustness
- Primary detection: Explicit `project_type` field (unambiguous)
- Secondary detection: Absence of `ado_provenance` (fallback)
- Tertiary verification: constraints.md documents synthetic nature
- Error handling: Clear markers prevent accidental ADO triggering

## References

- **UOW-003**: Intake artifact creation (prerequisite)
- **UOW-004**: This UoW (downstream verification)
- **UOW-005**: Integration test (depends on UOW-004)
- **Test File**: `tests/test_downstream_synthetic_mode.py`
- **Story**: `agent-context/TEST-AC-001/intake/story.yaml`
- **AC3**: "All workflow stages correctly skip ADO-specific operations for synthetic fixtures"
