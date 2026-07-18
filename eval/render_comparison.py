"""Human-readable rendering of a `ComparisonResult`.

`render_summary` reads ONLY the canonical `ComparisonResult` object — it
never re-derives a classification or re-applies policy thresholds. If the
canonical object says `mixed`, the summary says `mixed`; this module's job
is display, not judgment. Plain text, no ANSI, deterministic ordering
(everything is already grouped/sorted upstream in `eval.comparison`).
"""
from __future__ import annotations

from eval.comparison_schema import ComparisonResult, DeltaAvailability, MetricDelta, ReportIdentity


def _fmt_identity(identity: ReportIdentity) -> str:
    label = identity.label or "(unlabeled)"
    lines = [
        f"  {label}",
        f"    eval_run_id: {identity.eval_run_id if identity.eval_run_id is not None else 'unavailable'}",
        f"    candidate_version: {identity.candidate_version if identity.candidate_version is not None else 'unavailable'}",
        f"    benchmark_suite_id: {identity.benchmark_suite_id if identity.benchmark_suite_id is not None else 'unavailable'}",
        f"    trial_total: {identity.trial_total}",
    ]
    if identity.metadata:
        meta_str = ", ".join(f"{k}={v}" for k, v in sorted(identity.metadata.items()))
        lines.append(f"    metadata: {meta_str}")
    return "\n".join(lines)


def _fmt_num(value: float | int | None) -> str:
    """Display-only rounding to hide plain-float subtraction noise.

    The canonical `MetricDelta.absolute_delta`/`relative_delta` fields are
    never rounded — this only affects how they are printed here.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        rounded = round(value, 6)
        if rounded == int(rounded):
            return f"{int(rounded)}"
        return f"{rounded:g}"
    return str(value)


def _fmt_metric_delta(name: str, delta: MetricDelta) -> str:
    if delta.availability == DeltaAvailability.COMPUTED:
        if delta.relative_delta is not None:
            sign = "+" if delta.absolute_delta is not None and delta.absolute_delta >= 0 else ""
            return (
                f"    {name}: {delta.baseline_value} -> {delta.candidate_value} "
                f"(Δ {sign}{_fmt_num(delta.absolute_delta)}, {delta.relative_delta:+.1%})"
            )
        if delta.absolute_delta is not None:
            sign = "+" if delta.absolute_delta >= 0 else ""
            return (
                f"    {name}: {delta.baseline_value} -> {delta.candidate_value} "
                f"(Δ {sign}{_fmt_num(delta.absolute_delta)}, relative unavailable: {delta.reason})"
            )
        return f"    {name}: {delta.baseline_value} -> {delta.candidate_value}"
    return f"    {name}: unavailable ({delta.reason})"


def render_summary(result: ComparisonResult) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"COMPARISON RESULT  (schema v{result.comparison_schema_version})")
    lines.append("=" * 72)
    lines.append(f"Classification: {result.classification.value.upper()}")
    lines.append(f"Comparability:  {result.comparability.value}")
    lines.append("")

    lines.append("Baseline:")
    lines.append(_fmt_identity(result.baseline_identity))
    lines.append("Candidate:")
    lines.append(_fmt_identity(result.candidate_identity))
    lines.append("")

    if result.comparability_reasons:
        lines.append("Comparability notes:")
        for reason in result.comparability_reasons:
            lines.append(f"  - {reason}")
        lines.append("")

    if result.drift:
        lines.append("Configuration drift:")
        for finding in result.drift:
            lines.append(
                f"  [{finding.severity.value}] {finding.field}: {finding.baseline_value!r} -> {finding.candidate_value!r} "
                f"({finding.explanation})"
            )
        lines.append("")

    lines.append("Classification reasons:")
    if result.reasons:
        for reason in result.reasons:
            refs = []
            if reason.benchmark_ids:
                refs.append(f"benchmarks={reason.benchmark_ids}")
            if reason.ac_ids:
                refs.append(f"acs={reason.ac_ids}")
            if reason.metric_refs:
                refs.append(f"metrics={reason.metric_refs}")
            ref_str = f" ({', '.join(refs)})" if refs else ""
            lines.append(f"  [{reason.severity.value}] {reason.code}: {reason.summary}{ref_str}")
    else:
        lines.append("  (none)")
    lines.append("")

    if result.critical_ac_regressions:
        lines.append("Critical acceptance-criteria regressions:")
        for d in result.critical_ac_regressions:
            lines.append(f"  - {d.benchmark_id}/{d.ac_id}: {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    if result.easy_regressions:
        lines.append("Easy-tier benchmark regressions:")
        for d in result.easy_regressions:
            lines.append(f"  - {d.benchmark_id}: {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    if result.regressed_benchmarks:
        lines.append("Regressed benchmarks:")
        for d in result.regressed_benchmarks:
            lines.append(f"  - {d.benchmark_id} (difficulty={d.candidate_difficulty}): {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    if result.improved_benchmarks:
        lines.append("Improved benchmarks:")
        for d in result.improved_benchmarks:
            lines.append(f"  - {d.benchmark_id} (difficulty={d.candidate_difficulty}): {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    if result.regressed_acs:
        lines.append("Regressed acceptance criteria:")
        for d in result.regressed_acs:
            lines.append(f"  - {d.benchmark_id}/{d.ac_id} (critical={d.critical}): {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    if result.improved_acs:
        lines.append("Improved acceptance criteria:")
        for d in result.improved_acs:
            lines.append(f"  - {d.benchmark_id}/{d.ac_id} (critical={d.critical}): {d.baseline_status} -> {d.candidate_status}")
        lines.append("")

    lines.append("Scorecard deltas:")
    for category in ("capability", "reliability", "efficiency"):
        section = result.scorecard_deltas.get(category, {})
        if not section:
            continue
        lines.append(f"  {category}:")
        for name in sorted(section):
            lines.append(_fmt_metric_delta(name, section[name]))
    lines.append("")

    if result.evidence_regressions:
        lines.append("Evidence regressions:")
        for e in result.evidence_regressions:
            state = "lost" if e.regressed else "unavailable"
            lines.append(f"  - [{e.requirement}] {e.field}: {state} ({e.baseline_value} -> {e.candidate_value})")
        lines.append("")

    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
