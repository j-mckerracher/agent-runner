"""Centralized, documented policy for comparing two v0.2 evaluation reports.

`eval.comparison` and `eval.classification` never hard-code a threshold or a
budget inline — every materiality threshold, efficiency budget, evidence
requirement, and identity/drift field lives here so the policy can be
inspected, unit-tested, and (later) tuned without touching comparison logic.

This is deliberately small. It is not a general rule engine — it is a fixed
set of named knobs with documented defaults, matching the scope of Prompt 5.
See `docs/evaluation-comparison.md` for the full rationale of each default.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


class PolicyConfigError(ValueError):
    """Raised when a `ComparisonPolicy` is configured with invalid values."""


class ReasonCode:
    """Stable, documented classification reason codes.

    Living here (rather than in `eval.classification`) keeps the reason
    vocabulary next to the policy it is derived from, and lets both
    `eval.comparison` and `eval.classification` reference the same names
    without importing each other's internals.
    """

    EASY_BENCHMARK_REGRESSION = "easy_benchmark_regression"
    CRITICAL_AC_REGRESSION = "critical_ac_regression"
    CRITICAL_AC_IMPROVEMENT = "critical_ac_improvement"
    AC_PASS_RATE_DROP = "ac_pass_rate_drop"
    AC_PASS_RATE_GAIN = "ac_pass_rate_gain"
    WORKFLOW_FAILURE_RATE_INCREASE = "workflow_failure_rate_increase"
    WORKFLOW_FAILURE_RATE_DECREASE = "workflow_failure_rate_decrease"
    REQUIRED_EVIDENCE_LOST = "required_evidence_lost"
    REQUIRED_EVIDENCE_UNAVAILABLE = "required_evidence_unavailable"
    CONDITIONAL_EVIDENCE_LOST = "conditional_evidence_lost"
    EVIDENCE_GAINED = "evidence_gained"
    RUNTIME_HARD_BUDGET_BREACH = "runtime_hard_budget_breach"
    RUNTIME_SOFT_BUDGET_BREACH = "runtime_soft_budget_breach"
    COST_HARD_BUDGET_BREACH = "cost_hard_budget_breach"
    COST_SOFT_BUDGET_BREACH = "cost_soft_budget_breach"
    RELIABILITY_REGRESSION = "reliability_regression"
    RELIABILITY_IMPROVEMENT = "reliability_improvement"
    RELIABILITY_UNAVAILABLE = "reliability_unavailable"
    MATERIAL_DRIFT = "material_drift"
    INCOMPATIBLE_DRIFT = "incompatible_drift"
    INFORMATIONAL_DRIFT = "informational_drift"
    ZERO_TRIALS = "zero_trials"
    TRIAL_COUNT_MISMATCH = "trial_count_mismatch"
    SUITE_MISMATCH = "benchmark_suite_mismatch"
    NO_SHARED_IDENTITY = "no_shared_identity"
    CLEAN_IMPROVEMENT = "clean_improvement"
    NO_MATERIAL_CHANGE = "no_material_change"
    MIXED_MOVEMENT = "mixed_movement"
    UNRESOLVED_MATERIAL_REGRESSION = "unresolved_material_regression"


@dataclass(frozen=True)
class ComparisonPolicy:
    """Documented defaults for comparison materiality, budgets, and drift.

    All threshold fields are expressed the same way the underlying metric is
    expressed (e.g. `ac_pass_rate` is a 0..1 fraction, so its materiality
    threshold is also a 0..1 fraction — 0.05 means "5 percentage points").
    """

    # --- capability materiality (fractions of a 0..1 rate) ---
    ac_pass_rate_materiality: float = 0.05
    workflow_failure_rate_materiality: float = 0.05

    # --- efficiency budgets: relative % increase, candidate vs baseline ---
    runtime_increase_soft_budget_pct: float = 15.0
    runtime_increase_hard_budget_pct: float = 50.0
    cost_increase_soft_budget_pct: float = 15.0
    cost_increase_hard_budget_pct: float = 50.0

    # --- reliability materiality ---
    trial_pass_rate_materiality: float = 0.10

    # --- trial-count comparability rule ---
    # A benchmark case's trial count is `len(case.trial_results)`. Two
    # comparable-only-when-supported rules apply, both documented in
    # `docs/evaluation-comparison.md`:
    #   1. Per-case: reliability/runtime comparison for a matched case is
    #      computed only when both sides have >= `min_trials` trials AND
    #      max(count)/min(count) <= `max_trial_count_ratio`. Otherwise that
    #      case's reliability/runtime deltas are marked incomparable — the
    #      case's pass/fail category is unaffected.
    #   2. Report-level: if either report has zero recorded trials across
    #      every benchmark case, the whole comparison is treated as
    #      insufficient evidence and yields `inconclusive`.
    min_trials: int = 1
    max_trial_count_ratio: float = 2.0

    # --- evidence policy ---
    # Which `TraceabilityScore` fields are contractually required vs.
    # conditionally expected vs. purely optional. See docs for the
    # required/conditional/optional distinction and how each is scored.
    required_evidence_fields: tuple[str, ...] = ("trace_present",)
    conditional_evidence_fields: tuple[str, ...] = (
        "test_output_present",
        "final_artifact_refs_present",
        "final_diff_ref_present",
    )
    optional_evidence_fields: tuple[str, ...] = ("prompt_hashes_present",)

    # --- identity / drift ---
    # Fixed, documented metadata keys considered for identity display and
    # drift detection. Free-form `summary` text is never parsed.
    identity_metadata_keys: tuple[str, ...] = (
        "runner",
        "model",
        "workflow_config",
        "prompt_hash",
        "config_hash",
        "loop_limit",
        "target_repo_version",
    )
    # Which of the identity_metadata_keys, if they differ, are "material"
    # drift (potentially undermines trust in the comparison but does not by
    # itself make it incomparable). Any identity_metadata_keys not listed
    # here are treated as informational drift when they differ.
    material_drift_metadata_keys: tuple[str, ...] = (
        "runner",
        "model",
        "workflow_config",
        "prompt_hash",
        "config_hash",
        "loop_limit",
        "target_repo_version",
    )

    def validate(self) -> None:
        errors: list[str] = []
        if self.ac_pass_rate_materiality < 0:
            errors.append("ac_pass_rate_materiality must be >= 0")
        if self.workflow_failure_rate_materiality < 0:
            errors.append("workflow_failure_rate_materiality must be >= 0")
        if self.trial_pass_rate_materiality < 0:
            errors.append("trial_pass_rate_materiality must be >= 0")
        if self.runtime_increase_soft_budget_pct < 0:
            errors.append("runtime_increase_soft_budget_pct must be >= 0")
        if self.cost_increase_soft_budget_pct < 0:
            errors.append("cost_increase_soft_budget_pct must be >= 0")
        if self.runtime_increase_hard_budget_pct < self.runtime_increase_soft_budget_pct:
            errors.append("runtime_increase_hard_budget_pct must be >= runtime_increase_soft_budget_pct")
        if self.cost_increase_hard_budget_pct < self.cost_increase_soft_budget_pct:
            errors.append("cost_increase_hard_budget_pct must be >= cost_increase_soft_budget_pct")
        if self.min_trials < 1:
            errors.append("min_trials must be >= 1")
        if self.max_trial_count_ratio < 1:
            errors.append("max_trial_count_ratio must be >= 1")
        overlap = (set(self.required_evidence_fields) | set(self.conditional_evidence_fields)) & set(
            self.optional_evidence_fields
        )
        if overlap:
            errors.append(f"evidence fields cannot be both optional and required/conditional: {sorted(overlap)}")
        unknown_material = set(self.material_drift_metadata_keys) - set(self.identity_metadata_keys)
        if unknown_material:
            errors.append(f"material_drift_metadata_keys must be a subset of identity_metadata_keys: {sorted(unknown_material)}")
        if errors:
            raise PolicyConfigError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


DEFAULT_POLICY = ComparisonPolicy()
