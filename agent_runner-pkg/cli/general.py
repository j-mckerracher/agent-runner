from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[run_general] {_ts()} {msg}", flush=True)


def _build_claude_code_cmd(args: argparse.Namespace) -> list[str]:
    cmd = ["claude", "--print"]
    if args.model:
        cmd += ["--model", args.model]
    if args.agent:
        cmd += ["--agent", args.agent]
    cmd += ["--dangerously-skip-permissions"]
    cmd.append(args.prompt)
    return cmd


def _build_github_copilot_cmd(args: argparse.Namespace) -> list[str]:
    cmd = ["copilot"]
    if args.model:
        cmd += ["--model", args.model]
    if args.agent:
        cmd += ["--agent", args.agent]
    cmd += ["--allow-all-tools", "--allow-all-paths", "-p", args.prompt]
    return cmd


_BACKEND_BUILDERS = {
    "claude-code": _build_claude_code_cmd,
    "github-copilot": _build_github_copilot_cmd,
}

_BACKEND_CLI = {
    "claude-code": "claude",
    "github-copilot": "copilot",
}


def _resolve_repo(name_or_path: str) -> Path | None:
    p = Path(name_or_path)
    if p.is_absolute():
        return p.resolve() if p.is_dir() else None
    candidate = Path.home() / "Code" / name_or_path
    return candidate.resolve() if candidate.is_dir() else None


def _write_summary(path: Path, *, exit_code: int, elapsed: float, backend: str) -> None:
    summary = {
        "status": "pass" if exit_code == 0 else "fail",
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 2),
        "backend": backend,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        log(f"Warning: could not write summary to {path}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an AI agent against a repository with a freeform prompt.",
        epilog=(
            "Backends and available models:\n"
            "  github-copilot  gpt-5.4 | gpt-5.3-codex | gpt-5.2 | gpt-5.1\n"
            "                  gpt-5.4-mini | gpt-5-mini | gpt-4.1\n"
            "                  claude-sonnet-4.6 | claude-opus-4.6 | claude-haiku-4.5\n"
            "  claude-code     claude-opus-4-5 | claude-sonnet-4-5 | claude-haiku-4-5\n"
            "\n"
            "Example:\n"
            "  python3 run_general.py \\\n"
            "    --backend github-copilot --model claude-sonnet-4.6 \\\n"
            "    --prompt \"Fix the failing unit tests\" \\\n"
            "    --repo mcs-products-mono-ui"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--backend",
        required=True,
        choices=sorted(_BACKEND_BUILDERS),
        help="AI backend to invoke (claude-code or github-copilot)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model identifier passed to the backend CLI "
        "(e.g. claude-sonnet-4.6, gpt-5.2)",
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="The prompt text to send to the agent",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help=(
            "Repository name (e.g. mcs-products-mono-ui, searched under ~/Code) "
            "or absolute path to the repository"
        ),
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Path to an .agent.md file (relative to repo or absolute)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        type=Path,
        help="Path to write a JSON summary on completion",
    )
    args = parser.parse_args(argv)

    repo = _resolve_repo(args.repo)
    if repo is None:
        log(
            f"Error: repo '{args.repo}' not found. Checked ~/Code/{args.repo} "
            "and as an absolute path. Provide a name that exists under ~/Code "
            "or an absolute directory path."
        )
        return 1

    cli = _BACKEND_CLI[args.backend]
    if shutil.which(cli) is None:
        log(f"Error: {cli!r} CLI not found on PATH")
        return 1

    builder = _BACKEND_BUILDERS[args.backend]
    cmd = builder(args)
    log(f"Running: {' '.join(cmd)}")
    log(f"CWD: {repo}")

    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        print(line, flush=True)

    proc.wait()
    elapsed = time.monotonic() - start
    exit_code = proc.returncode
    log(f"Finished: exit_code={exit_code}  elapsed={elapsed:.1f}s")

    if args.output_json:
        _write_summary(
            args.output_json,
            exit_code=exit_code,
            elapsed=elapsed,
            backend=args.backend,
        )

    return 0 if exit_code == 0 else 1
