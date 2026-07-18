"""v0.2 command surface: `python -m eval.cli <run|baseline|compare|ci> ...`.

This module is a thin translator + reporter. It never reimplements the
suite loop, scoring, comparison, or classification logic that already lives
in `eval/runner.py`, `eval/comparison.py`, and `eval/classification.py`.
`run`/`baseline` translate CLI flags into an `eval.runner`-shaped argv list
and call `eval.runner.main(argv)`; `compare`/`ci` call `load_and_compare`.

Exit codes are decided exclusively by `eval/exit_codes.py`. See
`docs/evaluation.md` for the full command reference and exit-code table.

No optional-integration (Opik, server) import happens at module import time
or in any code path reachable from `main()` — see `tests/test_cli_isolation.py`.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from eval.comparison import load_and_compare
from eval.comparison_policy import DEFAULT_POLICY
from eval.comparison_schema import CompareInputError
from eval.exit_codes import (
    EXIT_EVAL_FAILED,
    EXIT_OK,
    EXIT_REPORT_FAILED,
    EXIT_USAGE,
    compare_input_error_to_exit_code,
    result_to_exit_code,
)
from eval.render_comparison import render_summary
from eval.render_report import render_eval_summary
from eval.report_schema import EvalReport, ReportValidationError


def _print_err(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m eval.cli", description="v0.2 evaluation command surface.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_run_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--suite", type=Path, required=True, help="Benchmarks directory (maps to --benchmarks-dir).")
        sub.add_argument("--benchmark", action="append", default=[], help="Benchmark name to run; repeat for multiple.")
        sub.add_argument("--difficulty", nargs="+", choices=["easy", "medium", "hard"], default=None)
        sub.add_argument("--trials", type=int, default=1, help="Number of trials per benchmark (maps to --runs).")
        sub.add_argument("--output-dir", type=Path, required=True, help="Reports directory (maps to --reports-dir).")
        sub.add_argument("--repo", default=None)
        sub.add_argument("--sha", default=None)
        sub.add_argument("--runner", default=None)
        sub.add_argument("--model", default=None)

    run_parser = subparsers.add_parser("run", help="Run a candidate evaluation and produce a v0.2 report.")
    add_run_args(run_parser)

    baseline_parser = subparsers.add_parser(
        "baseline", help="Run an evaluation and save its report as a baseline (there is no 'baseline select')."
    )
    add_run_args(baseline_parser)
    baseline_parser.add_argument("--output", type=Path, required=True, help="Where to copy the produced baseline report.")

    compare_parser = subparsers.add_parser("compare", help="Compare two existing v0.2 reports (inspection only).")
    compare_parser.add_argument("--baseline", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, required=True, help="Where to write the comparison JSON.")

    ci_parser = subparsers.add_parser("ci", help="Compare two existing v0.2 reports and gate on the result.")
    ci_parser.add_argument("--baseline", type=Path, required=True)
    ci_parser.add_argument("--candidate", type=Path, required=True)
    ci_parser.add_argument("--comparison-output", type=Path, required=True)
    ci_parser.add_argument("--allow-inconclusive", action="store_true", default=False)
    ci_parser.add_argument(
        "--allow-mixed", action=argparse.BooleanOptionalAction, default=True, help="Treat a mixed classification as non-gating (default: true)."
    )

    return parser


# --------------------------------------------------------------------------
# run / baseline
# --------------------------------------------------------------------------


def _build_runner_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = [
        "--benchmarks-dir",
        str(args.suite),
        "--reports-dir",
        str(args.output_dir),
        "--runs",
        str(args.trials),
    ]
    for name in args.benchmark:
        argv.extend(["--benchmark", name])
    if args.difficulty:
        argv.append("--difficulty")
        argv.extend(args.difficulty)
    if args.repo:
        argv.extend(["--repo", args.repo])
    if args.sha:
        argv.extend(["--sha", args.sha])
    if args.runner:
        argv.extend(["--runner", args.runner])
    if args.model:
        argv.extend(["--model", args.model])
    return argv


def _run_candidate(args: argparse.Namespace) -> tuple[int, EvalReport | None, Path | None]:
    """Run `eval.runner.main` and load the resulting `latest.json`.

    Returns `(exit_code, report_or_none, report_path_or_none)`. Exit codes
    used here are only USAGE/EVAL_FAILED/REPORT_FAILED/OK — the caller
    decides what OK means for its own subcommand (run vs baseline).
    """
    if args.trials < 1:
        _print_err("error: --trials must be >= 1")
        return EXIT_USAGE, None, None

    import eval.runner as runner  # local import: keeps module-load side-effect-free

    argv = _build_runner_argv(args)
    rc = runner.main(argv)

    # `runner.main`'s return code conflates two distinct failure modes: (a)
    # the eval harness itself crashed/aborted before a report could be
    # produced, and (b) the harness ran to completion and wrote a valid
    # report that simply records one or more failed benchmark cases. Only
    # (a) is a CLI-level eval failure (exit 3) -- (b) is normal report
    # content, not a CLI failure, and a run producing such a report exits 0
    # so `run`/`baseline` stay usable in CI even when the current candidate
    # fails part of the suite. We disambiguate by checking whether a valid
    # `latest.json` was actually written, regardless of `rc`.
    latest_path = Path(args.output_dir) / "latest.json"
    if not latest_path.exists():
        if rc != 0:
            _print_err(f"error: evaluation run failed (eval.runner.main returned {rc})")
            return EXIT_EVAL_FAILED, None, None
        _print_err(f"error: evaluation run reported success but {latest_path} was not written")
        return EXIT_REPORT_FAILED, None, None

    try:
        report = EvalReport.from_json(latest_path.read_text(encoding="utf-8"))
    except ReportValidationError as exc:
        if rc != 0:
            _print_err(f"error: evaluation run failed (eval.runner.main returned {rc})")
            return EXIT_EVAL_FAILED, None, None
        _print_err(f"error: report at {latest_path} failed schema validation: {'; '.join(exc.errors)}")
        return EXIT_REPORT_FAILED, None, None

    return EXIT_OK, report, latest_path


def cmd_run(args: argparse.Namespace) -> int:
    exit_code, report, report_path = _run_candidate(args)
    if report is None:
        return int(exit_code)
    print(render_eval_summary(report, report_path=str(report_path)))
    return int(exit_code)


def cmd_baseline(args: argparse.Namespace) -> int:
    exit_code, report, report_path = _run_candidate(args)
    if report is None:
        return int(exit_code)
    # Copy only the final validated bytes that `write_eval_report` already
    # produced -- never re-serialize by hand, so identity/evidence-relative
    # paths in the copy never diverge from what was actually written.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(report_path, args.output)
    print(render_eval_summary(report, report_path=str(args.output)))
    print(f"Baseline report saved to {args.output}")
    return int(exit_code)


# --------------------------------------------------------------------------
# compare / ci
# --------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        result = load_and_compare(args.baseline, args.candidate, policy=DEFAULT_POLICY)
    except CompareInputError as exc:
        _print_err(f"error: {exc}")
        for detail in exc.errors:
            _print_err(f"  - {detail}")
        return int(compare_input_error_to_exit_code(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.to_json(indent=2) + "\n", encoding="utf-8")
    print(render_summary(result))

    # `compare` is an inspection command: both inconclusive and mixed are
    # non-gating here. Only genuine incompatibility (comparability !=
    # COMPARABLE) is nonzero without an explicit flag. Gating policy lives
    # in `ci`.
    return int(result_to_exit_code(result, allow_inconclusive=True, allow_mixed=True))


def cmd_ci(args: argparse.Namespace) -> int:
    try:
        result = load_and_compare(args.baseline, args.candidate, policy=DEFAULT_POLICY)
    except CompareInputError as exc:
        _print_err(f"error: {exc}")
        for detail in exc.errors:
            _print_err(f"  - {detail}")
        return int(compare_input_error_to_exit_code(exc))

    # Comparison JSON is always written before the exit code is computed --
    # writing never depends on which code will be returned, so evidence is
    # never discarded on a hard-gate failure.
    args.comparison_output.parent.mkdir(parents=True, exist_ok=True)
    args.comparison_output.write_text(result.to_json(indent=2) + "\n", encoding="utf-8")
    print(render_summary(result))

    return int(
        result_to_exit_code(
            result,
            allow_inconclusive=args.allow_inconclusive,
            allow_mixed=args.allow_mixed,
        )
    )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

_HANDLERS = {
    "run": cmd_run,
    "baseline": cmd_baseline,
    "compare": cmd_compare,
    "ci": cmd_ci,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed usage/error text; normalize the exit
        # code to the documented USAGE code rather than argparse's raw 2
        # (which happens to already equal EXIT_USAGE, but we go through the
        # exit-codes module so the mapping stays centralized and explicit).
        code = exc.code if isinstance(exc.code, int) else 1
        return int(EXIT_USAGE) if code != 0 else int(EXIT_OK)

    handler = _HANDLERS[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
