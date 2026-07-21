"""Prompt 21 — Evidence artifact registry.

Aggregation surface for the three evidence artifact adapters introduced in
Prompt 21.  Import from here when you need all three at once, or when you
want to look up an adapter by its ``ARTIFACT_TYPE`` string.

Does NOT touch PLANNING_ARTIFACTS / REPORT_ARTIFACTS (those live in
``artifacts.payloads``).

Public surface
--------------
::

    from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
    from eval.evidence_artifacts import EvalReportArtifact, TraceArtifact, FinalDiffArtifact
"""

from __future__ import annotations

from artifacts.evidence import FinalDiffArtifact
from eval.report_artifact import EvalReportArtifact
from telemetry.trace_artifact import TraceArtifact

#: Tuple of the three evidence artifact adapter classes, in canonical order.
#: Discoverable by ``ARTIFACT_TYPE`` class variable.
EVIDENCE_ARTIFACTS = (EvalReportArtifact, TraceArtifact, FinalDiffArtifact)

__all__ = [
    "EVIDENCE_ARTIFACTS",
    "EvalReportArtifact",
    "FinalDiffArtifact",
    "TraceArtifact",
]
