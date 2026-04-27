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
