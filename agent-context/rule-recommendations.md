# Rule Recommendations Ledger

> Managed by Agent 11 (Lessons Optimizer Hyperagent). High-confidence rules are injected directly into agent files; medium/low-confidence rules are recorded here for human review before injection.

---

## REC-001 — check-test-harnesses.py false negatives in mcs-products-mono-ui (Medium Confidence)

**Session**: WI-5035632-lessons-optimizer-001
**Date**: 2026-04-18
**Target Agent**: `08-implementation-evaluator.agent.md`
**Confidence**: Medium — this is a tool-level defect, not an agent reasoning failure. Injection would encode a workaround for a broken script rather than fixing the root cause. Recommended action is to fix the script first.

**Observed Pattern**:
The `check-test-harnesses.py` script consistently exits 1 (false negative) for components in `mcs-products-mono-ui` because the script appends `*.test-harness.ts` but the codebase convention is `*.component.test-harness.ts`. Two independent evaluators (UOW-003, UOW-004) overrode the gate result by directly verifying the file exists on disk. Each override required additional reasoning work and carries a risk of false-passing a genuinely missing harness.

**Evidence**:
- UOW-003 eval: `check-test-harnesses.py exited 1 with expected_harness='unsaved-changes-dialog.test-harness.ts'`. Actual file: `unsaved-changes-dialog.component.test-harness.ts`. Override justified by cross-reference with `paperwork-label-dialog.component.test-harness.ts` in same directory.
- UOW-004 eval: Same script, same pattern, same override.

**Recommended Rule** (pending script fix — inject if script is not fixed within 2 sprints):
> When `check-test-harnesses.py` exits non-zero for a component in the mcs-products-mono-ui codebase, do NOT treat the exit code as a hard gate failure without first checking the filesystem directly for a `*.component.test-harness.ts` file in the same directory as the component. The script uses the wrong suffix (`*.test-harness.ts`) for this codebase's naming convention. If the `*.component.test-harness.ts` file is confirmed to exist and contains the required selectors, override the gate to pass and document the script anomaly in `tool_anomalies`. If no harness file is found by either naming pattern, fail the gate as normal.

**Preferred Fix**: Update `check-test-harnesses.py` to glob for `*.component.test-harness.ts` (or any `*test-harness.ts` variant) in the component's directory, rather than constructing a fixed expected path by string manipulation.

**Source Lessons**: eval_impl_1.json for UOW-003 (tool_anomalies[0]) and UOW-004 (tool_anomalies[0])

---
