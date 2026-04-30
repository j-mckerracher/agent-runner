# Intake Constraints and Context
**Change ID**: TEST-AC-001  
**Created**: 2026-04-22  
**Source**: Local synthetic fixture (not a live Azure DevOps work item)

---

## Story Context

This is an intentionally minimal, deterministic story designed to smoke-test the entire agent-runner workflow pipeline without requiring Azure DevOps connectivity or live infrastructure. All requirements are self-contained in this local fixture.

---

## Acceptance Criteria Summary

| Criterion | Status |
|-----------|--------|
| AC1: Workflow can start from local fixture instead of ADO | Defined |
| AC2: Intake normalizes criteria into canonical artifacts | Defined |
| AC3: ADO-specific actions are skipped for synthetic input | Defined |

---

## Technical Requirements

### Explicit Requirements from Fixture

1. **Fixture-First Path**: The workflow must support `--story-file agent-context/test-fixtures/synthetic_story.json` as an alternative to Azure DevOps work item links.
2. **Artifact Preservation**: Raw synthetic input must be preserved in `raw_input` section of `story.yaml` for auditability.
3. **No ADO Dependency**: The synthetic path must not attempt Azure CLI calls or ADO authentication.
4. **Deterministic Input**: The fixture is intentionally small and unchanged across test runs.

### Non-Functional Requirements from Fixture

1. **Fast Failure**: The synthetic workflow path should fail fast on malformed fixture input (e.g., missing required fields, invalid JSON).
2. **Downstream Compatibility**: Generated intake artifacts must remain compatible with existing task generation, planning, assignment, implementation, and QA stages.

---

## Examples from Fixture

1. Run the workflow locally with `--story-file agent-context/test-fixtures/synthetic_story.json`.
2. Use the generated intake artifacts to verify task generation and assignment work without Azure connectivity.

---

## Constraints from Fixture

1. This story exists only for workflow testing — do not promote artifacts to production scenarios.
2. Keep any code changes minimal and easy to verify.
3. Do not require Azure CLI or Azure DevOps access for this scenario.

---

## Project Structure

- **Code Repo**: `/Users/mckerracher.joshua/Code/Mine/agent-runner` (Python CLI project)
- **Fixture Location**: `agent-context/test-fixtures/synthetic_story.json`
- **Intake Output Location**: `agent-context/TEST-AC-001/intake/`

---

## Open Questions / Ambiguities

**None identified at intake time.** The fixture is fully self-described with all required context present. All acceptance criteria are explicit and concrete.

---

## Known Gaps or Future Scope

- This story validates only the local fixture path; live Azure DevOps integration is tested separately.
- No external dependencies (APIs, databases, authentication services) are required for this scenario.

---

## Fixture Provenance

| Field | Value |
|-------|-------|
| Source File | `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/test-fixtures/synthetic_story.json` |
| Purpose | Local end-to-end workflow testing |
| Owner | agent-runner |
| Date Ingested | 2026-04-22 |

---

## Intake Validation

✓ Fixture successfully read and normalized  
✓ Acceptance criteria normalized to AC1, AC2, AC3  
✓ No ADO metadata present (ADO-specific sections skipped)  
✓ Raw input preserved in story.yaml  
✓ All artifacts created in canonical locations  
✓ No external dependencies identified
