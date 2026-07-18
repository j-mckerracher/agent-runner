"""Centralized exit-code contract for the v0.2 CLI (Prompt 7).

Nothing outside this module decides an exit code. Renderers and handlers
report facts (a `ComparisonResult`, a `CompareInputError`); only the two
functions here translate those facts into a process exit code. This keeps
the exit-code semantics documented in exactly one place and testable in
isolation from argument parsing or I/O.

Exit codes (verbatim from the Prompt 7 spec):

    0  operation completed successfully and no configured hard regression
       gate failed
    2  invalid CLI usage, invalid input, or invalid configuration
    3  evaluation execution failed before a valid canonical report could be
       completed
    4  canonical report generation or report validation failed
    5  comparison could not be completed because reports were incompatible
       or required data was missing
    6  candidate was classified as regressed or another configured hard gate
       failed
"""
from __future__ import annotations

from enum import IntEnum

from eval.comparison_schema import ComparisonResult, Classification, Comparability, CompareInputError


class ExitCode(IntEnum):
    OK = 0
    USAGE = 2
    EVAL_FAILED = 3
    REPORT_FAILED = 4
    COMPARE_INCOMPLETE = 5
    REGRESSION = 6


EXIT_OK = ExitCode.OK
EXIT_USAGE = ExitCode.USAGE
EXIT_EVAL_FAILED = ExitCode.EVAL_FAILED
EXIT_REPORT_FAILED = ExitCode.REPORT_FAILED
EXIT_COMPARE_INCOMPLETE = ExitCode.COMPARE_INCOMPLETE
EXIT_REGRESSION = ExitCode.REGRESSION


def compare_input_error_to_exit_code(err: CompareInputError) -> ExitCode:
    """Map a `CompareInputError` to an exit code.

    `side="policy"` is a configuration/usage problem (bad `ComparisonPolicy`
    construction) -> USAGE. `side="baseline"`/`side="candidate"` means the
    report itself failed to load or validate -> REPORT_FAILED.
    """
    if err.side == "policy":
        return EXIT_USAGE
    if err.side in ("baseline", "candidate"):
        return EXIT_REPORT_FAILED
    # Defensive: any future `side` value is treated as a report problem
    # rather than silently succeeding.
    return EXIT_REPORT_FAILED


def result_to_exit_code(
    result: ComparisonResult,
    *,
    allow_inconclusive: bool = False,
    allow_mixed: bool = True,
) -> ExitCode:
    """Map a produced `ComparisonResult` to an exit code.

    Comparability is checked first and independently of classification: a
    `ComparisonResult` that is not COMPARABLE means the comparison itself is
    structurally incomplete (e.g. no shared benchmark_id, degraded evidence)
    -- this is the real "incompatible reports" case and always maps to
    COMPARE_INCOMPLETE regardless of what classification was still derived.
    """
    if result.comparability != Comparability.COMPARABLE:
        return EXIT_COMPARE_INCOMPLETE

    classification = result.classification
    if classification == Classification.REGRESSED:
        return EXIT_REGRESSION
    if classification == Classification.INCONCLUSIVE:
        return EXIT_OK if allow_inconclusive else EXIT_REGRESSION
    if classification == Classification.MIXED:
        return EXIT_OK if allow_mixed else EXIT_REGRESSION
    # IMPROVED / UNCHANGED
    return EXIT_OK
