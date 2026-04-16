"""Harness CLI entrypoint.

Subcommands:
  evaluate    — run evaluation cycle (dev or container mode)
  calibrate   — run multiple k-runs and compute a baseline band
  baseline    — read/write baseline bands
  replay      — alias for evaluate --cassette-mode replay
  record      — alias for evaluate --cassette-mode record
  report      — render a cycle report from an existing run directory
  materialize — materialize agent bundles to a target directory

Only evaluate, report, materialize, calibrate have real implementations.
replay, record are thin wrappers around evaluate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _resolve_dev_mode(args: argparse.Namespace) -> bool:
    """Resolve effective dev_mode from --mode, deprecated --dev-mode/--no-dev-mode flags.

    Priority: --mode > --no-dev-mode > --dev-mode > default (True).
    Emits deprecation warnings to stderr when legacy flags are used.
    """
    mode = getattr(args, "mode", None)
    dev_mode_flag = getattr(args, "dev_mode_flag", None)
    no_dev_mode_flag = getattr(args, "no_dev_mode_flag", False)

    if mode is not None:
        return mode == "dev"

    if no_dev_mode_flag:
        print(
            "WARNING: --no-dev-mode is deprecated; use --mode authoritative instead.",
            file=sys.stderr,
        )
        return False

    if dev_mode_flag:
        print(
            "WARNING: --dev-mode is deprecated; use --mode dev instead.",
            file=sys.stderr,
        )
        return True

    return True  # default


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Run an evaluation cycle."""
    from agent_runner_harness.corpus import load_all, load_task
    from agent_runner_harness.scheduler import RunOpts, run_cycle
    from agent_runner_harness.reporting import write_report

    corpus_dir = Path(args.corpus)
    runs_root = Path(args.runs_root)
    sources_dir = Path(args.sources)

    if args.task:
        task_dir = corpus_dir / args.task
        if not task_dir.exists():
            print(f"ERROR: Task directory not found: {task_dir}", file=sys.stderr)
            return 1
        tasks = [load_task(task_dir)]
    else:
        tasks = load_all(corpus_dir)

    if not tasks:
        print("No tasks found.", file=sys.stderr)
        return 1

    cassette_mode = getattr(args, "cassette_mode", "live")
    dev_mode = _resolve_dev_mode(args)
    image = getattr(args, "image", None)

    if not dev_mode and image is None:
        print(
            "ERROR: --mode authoritative requires --image <tag>. "
            "Build the image with: docker build -f docker/runner.Dockerfile -t <tag> .",
            file=sys.stderr,
        )
        return 3

    opts = RunOpts(
        k_runs=args.k,
        parallel=args.parallel,
        cassette_mode=cassette_mode,
        dev_mode=dev_mode,
        judge_model=args.judge_model,
        judge_stub=getattr(args, "judge_stub", False),
        sources_dir=sources_dir,
        runs_root=runs_root,
        corpus_dir=corpus_dir,
        image=image,
    )

    result = run_cycle(tasks, opts)
    write_report(result, runs_root)

    print(f"\nCycle {result.cycle_id} complete. Status: {result.status}")
    for r in result.run_results:
        status = "PASS" if r.get("overall_pass") else "FAIL"
        print(f"  [{status}] {r.get('task_id', '?')} ({r.get('run_id', '?')})")

    return 0 if result.status == "completed" else 1


def _cmd_calibrate(args: argparse.Namespace) -> int:
    """Run k-runs and compute/save a baseline band."""
    from agent_runner_harness.corpus import load_task
    from agent_runner_harness.scheduler import RunOpts, run_cycle
    from agent_runner_harness.baseline.manager import compute_band, save_band

    corpus_dir = Path(args.corpus)
    baselines_dir = Path(args.baselines_dir)

    if not args.task:
        print("ERROR: --task is required for calibrate", file=sys.stderr)
        return 1

    task = load_task(corpus_dir / args.task)
    opts = RunOpts(
        k_runs=args.k,
        dev_mode=_resolve_dev_mode(args),
        judge_model=args.judge_model,
        judge_stub=getattr(args, "judge_stub", False),
        sources_dir=Path(args.sources),
        runs_root=Path(args.runs_root),
        corpus_dir=corpus_dir,
    )
    result = run_cycle([task], opts)
    pass_rates = [r.get("overall_pass", False) for r in result.run_results]

    band = compute_band(
        pass_rates,
        judge_model=args.judge_model,
        task_id=task.id,
        task_version=task.version,
        reason=getattr(args, "reason", None) or "calibration",
    )
    dest = save_band(band, baselines_dir)
    print(f"Band saved to {dest}: low={band.low:.3f} mean={band.mean:.3f} high={band.high:.3f}")
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    """Read, write, or check baseline bands."""
    from agent_runner_harness.baseline.manager import load_band, save_band, compute_band

    baselines_dir = Path(args.baselines_dir)

    if args.baseline_action == "show":
        band = load_band(baselines_dir, args.task)
        if band is None:
            print(f"No band found for task {args.task!r}")
            return 1
        print(json.dumps(band.model_dump(), indent=2))
        return 0

    if args.baseline_action == "check":
        return _cmd_baseline_check(args, baselines_dir)

    print(f"Unknown baseline action: {args.baseline_action}", file=sys.stderr)
    return 1


def _cmd_baseline_check(args: argparse.Namespace, baselines_dir: Path) -> int:
    """Check all baselines against the current judge config."""
    from agent_runner_harness.grading.prompts import PROMPT_VERSION

    current_judge_model = getattr(args, "judge_model", "gpt-5.4-high")

    band_files = sorted(baselines_dir.glob("*.json"))
    if not band_files:
        print(f"No baseline files found in {baselines_dir}")
        return 0

    stale: list[str] = []
    for band_file in band_files:
        try:
            data = json.loads(band_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  [ERROR ] {band_file.name}: could not read — {exc}")
            stale.append(band_file.stem)
            continue

        task_id = data.get("task_id", band_file.stem)
        stored_model = data.get("judge_model", "")
        stored_reason = data.get("reason", "")

        model_ok = stored_model == current_judge_model
        prompt_ok = f"prompt_v={PROMPT_VERSION}" in stored_reason or PROMPT_VERSION == "1"

        if model_ok and prompt_ok:
            print(f"  [OK    ] {task_id} (model={stored_model})")
        else:
            reasons = []
            if not model_ok:
                reasons.append(f"judge_model: stored={stored_model!r} current={current_judge_model!r}")
            if not prompt_ok:
                reasons.append(f"prompt_version: stored reason lacks 'prompt_v={PROMPT_VERSION}'")
            print(f"  [STALE ] {task_id}: {'; '.join(reasons)}")
            stale.append(task_id)

    if stale:
        print(f"\n{len(stale)} baseline(s) need rebaseline. See docs/rebaseline.md")
        return 1
    print(f"\nAll {len(band_files)} baseline(s) are current.")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Render a report for an existing cycle run directory."""
    from agent_runner_harness.reporting import render_cycle_report

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"ERROR: Run directory not found: {run_dir}", file=sys.stderr)
        return 1

    # Build a minimal CycleResult from on-disk grading.json files
    import dataclasses

    @dataclasses.dataclass
    class _FakeCycle:
        cycle_id: str
        started_at: str
        completed_at: str
        status: str
        run_results: list
        errors: list

    grading_files = list(run_dir.glob("*/grading.json"))
    run_results = []
    for gf in grading_files:
        data = json.loads(gf.read_text(encoding="utf-8"))
        run_results.append(data)

    cycle = _FakeCycle(
        cycle_id=run_dir.name,
        started_at="",
        completed_at="",
        status="reported",
        run_results=run_results,
        errors=[],
    )
    print(render_cycle_report(cycle))
    return 0


def _cmd_materialize(args: argparse.Namespace) -> int:
    """Materialize agent bundles into a target directory."""
    from agent_runner_registry import load_bundles, resolve, materialize

    sources_dir = Path(args.sources)
    target_dir = Path(args.target)

    bundles_map = load_bundles(sources_dir)
    if args.agents:
        from agent_runner_shared.models import AgentRef
        refs = [AgentRef.parse(r) for r in args.agents]
        bundles = [bundles_map[r] for r in refs if r in bundles_map]
    else:
        bundles = list(bundles_map.values())

    manifest = materialize(bundles, target_dir)
    print(f"Materialized {len(manifest.agents)} agent(s) to {target_dir}")
    return 0


def _add_common_eval_args(parser: argparse.ArgumentParser) -> None:
    """Add common evaluation arguments to a parser."""
    parser.add_argument("--task", metavar="ID", help="Single task ID to evaluate")
    parser.add_argument("--corpus", default="task-corpus", metavar="DIR")
    parser.add_argument("--sources", default="agent-sources", metavar="DIR")
    parser.add_argument("--runs-root", default="runs", metavar="DIR")
    parser.add_argument("--k", type=int, default=1, metavar="INT", help="Runs per task")
    parser.add_argument("--parallel", type=int, default=1, metavar="INT")
    parser.add_argument("--judge-model", default="gpt-5.4-high", metavar="MODEL")
    parser.add_argument("--judge-stub", action="store_true",
                        help="Use stub judge (no LLM calls)")
    # Authoritative --mode flag (canonical)
    parser.add_argument(
        "--mode",
        choices=["authoritative", "dev"],
        default=None,
        metavar="{authoritative,dev}",
        help="Run mode: 'dev' (subprocess, default) or 'authoritative' (container)",
    )
    # Deprecated aliases kept for backwards compatibility
    parser.add_argument("--dev-mode", action="store_true", default=None,
                        dest="dev_mode_flag",
                        help="[deprecated] Use --mode dev instead")
    parser.add_argument("--no-dev-mode", dest="no_dev_mode_flag", action="store_true",
                        default=False,
                        help="[deprecated] Use --mode authoritative instead")
    parser.add_argument("--image", metavar="TAG",
                        help="Container image tag (required for --mode authoritative)")
    parser.add_argument("--cassette-mode", choices=["live", "record", "replay"],
                        default="live", metavar="MODE")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the harness CLI."""
    parser = argparse.ArgumentParser(
        prog="agent-runner-harness",
        description="Agent Runner evaluation harness",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # evaluate
    eval_parser = sub.add_parser("evaluate", help="Run evaluation cycle")
    _add_common_eval_args(eval_parser)

    # calibrate
    cal_parser = sub.add_parser("calibrate", help="Calibrate a task baseline")
    _add_common_eval_args(cal_parser)
    cal_parser.add_argument(
        "--baselines-dir", default="baselines", metavar="DIR"
    )
    cal_parser.add_argument(
        "--reason", default=None, metavar="STR",
        help="Human-readable reason for this calibration (stored in baseline)",
    )

    # baseline
    bl_parser = sub.add_parser("baseline", help="Read/write/check baseline bands")
    bl_parser.add_argument("baseline_action", choices=["show", "check"], help="Action to perform")
    bl_parser.add_argument("--task", metavar="ID", help="Task ID (required for 'show')")
    bl_parser.add_argument("--baselines-dir", default="baselines", metavar="DIR")
    bl_parser.add_argument("--judge-model", default="gpt-5.4-high", metavar="MODEL",
                           help="Current judge model to compare against (for 'check')")
    bl_parser.add_argument("--against", metavar="GIT_REF", default=None,
                           help="Compare baselines at this git ref (for 'check')")

    # replay
    replay_parser = sub.add_parser("replay", help="Evaluate with cassette replay")
    _add_common_eval_args(replay_parser)

    # record
    record_parser = sub.add_parser("record", help="Evaluate and record cassette")
    _add_common_eval_args(record_parser)

    # report
    report_parser = sub.add_parser("report", help="Render a cycle report")
    report_parser.add_argument("run_dir", metavar="RUN_DIR",
                               help="Path to the cycle run directory")

    # materialize
    mat_parser = sub.add_parser("materialize", help="Materialize agent bundles")
    mat_parser.add_argument("--sources", default="agent-sources", metavar="DIR")
    mat_parser.add_argument("--target", required=True, metavar="DIR")
    mat_parser.add_argument("--agents", nargs="+", metavar="REF",
                            help="Specific agent refs (name@version)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Harness CLI main entrypoint.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code integer.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "evaluate":
            return _cmd_evaluate(args)
        elif args.command == "calibrate":
            return _cmd_calibrate(args)
        elif args.command == "baseline":
            return _cmd_baseline(args)
        elif args.command == "replay":
            args.cassette_mode = "replay"
            return _cmd_evaluate(args)
        elif args.command == "record":
            args.cassette_mode = "record"
            return _cmd_evaluate(args)
        elif args.command == "report":
            return _cmd_report(args)
        elif args.command == "materialize":
            return _cmd_materialize(args)
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        return 4
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
