#!/usr/bin/env python3
"""Generate the three minimal eval benchmarks with an LLM.

This script intentionally does one job: create eval/benchmarks/<difficulty>/story.json
and hidden_tests.py for easy, medium, and hard work items. It does not run the
workflow and it does not introduce a second eval framework.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_models import RUNNER_MODEL_CHOICES, is_copilot_runner

BENCHMARKS_DIR = ROOT / "eval" / "benchmarks"
DIFFICULTIES = ("easy", "medium", "hard")
BLOCK_NAMES = ("story.json", "hidden_tests.py")


class BenchmarkGenerationError(RuntimeError):
    """Raised when an LLM response cannot be turned into a benchmark."""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def default(name: str, env_file: dict[str, str], fallback: str | None = None) -> str | None:
    return os.environ.get(name) or env_file.get(name) or fallback


def builtin_model_choices_for_runner(runner: str) -> tuple[str, ...] | None:
    if runner in RUNNER_MODEL_CHOICES:
        return RUNNER_MODEL_CHOICES[runner]
    if is_copilot_runner(runner):
        return RUNNER_MODEL_CHOICES["copilot"]
    return None


def resolve_model_override(
    runner: str,
    explicit_model: str | None,
    env_file: dict[str, str],
) -> str | None:
    if explicit_model is not None:
        return explicit_model

    inherited_model = default("EVAL_MODEL", env_file)
    if not inherited_model:
        return None

    allowed = builtin_model_choices_for_runner(runner)
    if allowed is None or inherited_model in allowed:
        return inherited_model
    return None


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout)


def tail(text: str | None, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else "..." + text[-limit:]


def prepare_workspace(repo: str, sha: str, workspace: Path) -> None:
    result = run_cmd(["git", "clone", repo, str(workspace)], cwd=ROOT)
    if result.returncode != 0:
        raise BenchmarkGenerationError(f"git clone failed:\n{tail(result.stderr)}")
    result = run_cmd(["git", "checkout", "--detach", sha], cwd=workspace)
    if result.returncode != 0:
        raise BenchmarkGenerationError(f"git checkout failed:\n{tail(result.stderr)}")


def build_prompt(*, difficulty: str, target_sha: str) -> str:
    difficulty_guidance = {
        "easy": (
            "A small, localized change. Prefer a deterministic validation rule, formatter behavior, "
            "pure function behavior, config default, parser edge case, or simple CLI behavior."
        ),
        "medium": (
            "A coordinated change across a few files or modules. Prefer behavior that exercises one "
            "integration seam, persistence boundary, adapter, or API/CLI path without requiring services."
        ),
        "hard": (
            "A cross-cutting but still bounded change. Prefer behavior that requires understanding "
            "multiple modules and preserving existing behavior. It must still be implementable in one eval run."
        ),
    }[difficulty]
    return f"""
You are creating one hidden-test benchmark for Agent Workbench.

You are currently inside a temporary clone of the target repository at gold-master commit {target_sha}.
Inspect the repository before choosing the benchmark. Do not modify files. Do not ask questions.

Create exactly one {difficulty.upper()} benchmark.
Difficulty calibration: {difficulty_guidance}

The benchmark must evaluate whether a coding workflow can complete a real work item sufficiently:
- all acceptance criteria must be testable;
- hidden tests must fail against the current gold-master behavior and pass only after the story is implemented correctly;
- tests must be deterministic and fast;
- no network calls, credentials, paid APIs, browser automation, long-running services, or destructive operations;
- prefer public behavior over brittle implementation details;
- avoid tasks that require large migrations, external databases, or broad product design decisions.

Return exactly these two blocks and no other prose:

<story.json>
{{
  "change_id": "EVAL-{difficulty.upper()}-001",
  "title": "Concise work-item title",
  "description": "User-story style description of the requested change.",
  "acceptance_criteria": [
    "AC1: Observable requirement.",
    "AC2: Regression requirement."
  ],
  "metadata": {{
    "difficulty": "{difficulty}",
    "target_sha": "{target_sha}",
    "generated": true
  }}
}}
</story.json>

<hidden_tests.py>
# pytest tests only. The file will be copied to the target repo root and run with:
# python -m pytest hidden_tests.py --disable-warnings -q
</hidden_tests.py>
""".strip()


def normalize_runner_output(runner: str, stdout: str) -> str:
    text = (stdout or "").strip()
    if not text:
        return text
    if runner == "claude":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
                return parsed["result"].strip()
        except json.JSONDecodeError:
            pass
    return text


def invoke_llm(
    *,
    runner: str,
    prompt: str,
    cwd: Path,
    model: str | None,
    timeout: int,
) -> str:
    runner = runner.strip().lower()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    if runner == "claude":
        if not shutil.which("claude"):
            raise BenchmarkGenerationError("claude CLI was not found on PATH")
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
        if model:
            cmd += ["--model", model]
    elif runner == "gemini":
        if not shutil.which("gemini"):
            raise BenchmarkGenerationError("gemini CLI was not found on PATH")
        cmd = ["gemini", "-p", prompt, "--output-format", "text", "--yolo"]
        if model:
            cmd += ["--model", model]
    elif runner == "copilot" or runner.startswith("copilot-"):
        cli_cmd = runner if runner.startswith("copilot-") else "copilot"
        if not shutil.which(cli_cmd):
            raise BenchmarkGenerationError(f"{cli_cmd} CLI was not found on PATH")
        cmd = [cli_cmd, "-p", prompt, "-s", "--yolo"]
        if runner == "copilot" and model:
            cmd += ["--model", model]
    else:
        raise BenchmarkGenerationError(
            f"Unsupported benchmark generator runner {runner!r}. Use claude, copilot, a copilot-* alias, or gemini."
        )

    result = run_cmd(cmd, cwd=cwd, timeout=timeout, env=env)
    if result.returncode != 0:
        raise BenchmarkGenerationError(
            f"{runner} benchmark generation failed with exit code {result.returncode}:\n{tail(result.stderr or result.stdout)}"
        )
    return normalize_runner_output(runner, result.stdout)


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def extract_block(text: str, block_name: str) -> str:
    pattern = re.compile(rf"<{re.escape(block_name)}>\s*(.*?)\s*</{re.escape(block_name)}>", re.DOTALL | re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        raise BenchmarkGenerationError(f"LLM response did not contain <{block_name}>...</{block_name}> block")
    return strip_code_fence(match.group(1))


def validate_story(story_text: str, *, difficulty: str, target_sha: str) -> dict[str, Any]:
    try:
        story = json.loads(story_text)
    except json.JSONDecodeError as exc:
        raise BenchmarkGenerationError(f"story.json is not valid JSON: {exc}") from exc
    if not isinstance(story, dict):
        raise BenchmarkGenerationError("story.json must contain a JSON object")
    required = ("change_id", "title", "description", "acceptance_criteria")
    missing = [key for key in required if not story.get(key)]
    if missing:
        raise BenchmarkGenerationError(f"story.json is missing required fields: {', '.join(missing)}")
    ac = story.get("acceptance_criteria")
    if not isinstance(ac, list) or not ac or not all(isinstance(item, str) and item.strip() for item in ac):
        raise BenchmarkGenerationError("acceptance_criteria must be a non-empty list of strings")
    metadata = story.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        story["metadata"] = metadata
    metadata.setdefault("difficulty", difficulty)
    metadata.setdefault("target_sha", target_sha)
    metadata.setdefault("generated", True)
    return story


def validate_hidden_tests(hidden_tests: str) -> str:
    content = hidden_tests.strip() + "\n"
    if "def test_" not in content:
        raise BenchmarkGenerationError("hidden_tests.py must define at least one pytest test function named test_*")
    try:
        tree = ast.parse(content, filename="hidden_tests.py")
    except SyntaxError as exc:
        raise BenchmarkGenerationError(f"hidden_tests.py has invalid Python syntax: {exc}") from exc

    blocked_modules = ("socket", "requests", "httpx", "aiohttp", "ftplib", "paramiko")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if any(name == blocked or name.startswith(blocked + ".") for blocked in blocked_modules):
                raise BenchmarkGenerationError(
                    f"hidden_tests.py imports blocked network/external module {name!r}; generated tests must be local and deterministic"
                )
    return content


def parse_benchmark_response(text: str, *, difficulty: str, target_sha: str) -> tuple[dict[str, Any], str]:
    story_text = extract_block(text, "story.json")
    tests_text = extract_block(text, "hidden_tests.py")
    return validate_story(story_text, difficulty=difficulty, target_sha=target_sha), validate_hidden_tests(tests_text)


def verify_gold_fails(workspace: Path, hidden_tests: str, timeout: int) -> None:
    test_file = workspace / "hidden_tests.py"
    old_content = test_file.read_text(encoding="utf-8") if test_file.exists() else None
    test_file.write_text(hidden_tests, encoding="utf-8")
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(workspace) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = run_cmd(
            [sys.executable, "-m", "pytest", "hidden_tests.py", "--disable-warnings", "-q"],
            cwd=workspace,
            timeout=timeout,
            env=env,
        )
    finally:
        if old_content is None:
            test_file.unlink(missing_ok=True)
        else:
            test_file.write_text(old_content, encoding="utf-8")

    if result.returncode == 0:
        raise BenchmarkGenerationError("hidden_tests.py unexpectedly passed against the gold-master commit")
    if result.returncode in {2, 3, 4, 5}:
        raise BenchmarkGenerationError(
            "hidden_tests.py did not produce normal failing tests against gold-master. "
            f"pytest exit code {result.returncode}:\n{tail(result.stdout + result.stderr)}"
        )


def write_benchmark(out_dir: Path, story: dict[str, Any], hidden_tests: str, *, force: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()) and not force:
        raise BenchmarkGenerationError(f"{out_dir} already exists; pass --force to overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "story.json").write_text(json.dumps(story, indent=2) + "\n", encoding="utf-8")
    (out_dir / "hidden_tests.py").write_text(hidden_tests, encoding="utf-8")


def generate_one(difficulty: str, workspace: Path, args: argparse.Namespace) -> None:
    prompt = build_prompt(difficulty=difficulty, target_sha=args.sha)
    last_error: Exception | None = None
    for attempt in range(1, args.attempts + 1):
        print(f"Generating {difficulty} benchmark (attempt {attempt}/{args.attempts})")
        try:
            output = invoke_llm(
                runner=args.runner,
                prompt=prompt,
                cwd=workspace,
                model=args.model,
                timeout=args.timeout,
            )
            story, hidden_tests = parse_benchmark_response(output, difficulty=difficulty, target_sha=args.sha)
            if args.verify_gold_fails:
                verify_gold_fails(workspace, hidden_tests, args.test_timeout)
            write_benchmark(args.output_dir / difficulty, story, hidden_tests, force=args.force)
            print(f"Wrote {args.output_dir / difficulty}")
            return
        except Exception as exc:  # noqa: BLE001 - show retryable parse/generation details.
            last_error = exc
            print(f"Generation attempt failed for {difficulty}: {exc}", file=sys.stderr)
    raise BenchmarkGenerationError(f"Unable to generate {difficulty} benchmark after {args.attempts} attempt(s): {last_error}")


def build_parser() -> argparse.ArgumentParser:
    env_file = read_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Seed eval/benchmarks/{easy,medium,hard} with LLM-generated stories and hidden tests.")
    parser.add_argument("--repo", default=default("EVAL_TARGET_REPO", env_file), help="Target Git repo path or URL. Defaults to EVAL_TARGET_REPO.")
    parser.add_argument("--sha", default=default("EVAL_TARGET_SHA", env_file), help="Gold-master commit SHA. Defaults to EVAL_TARGET_SHA.")
    parser.add_argument("--runner", default=default("EVAL_RUNNER", env_file, "claude"), help="LLM CLI to use: claude, copilot, copilot-* alias, or gemini.")
    parser.add_argument("--model", default=None, help="Optional model override for the selected runner.")
    parser.add_argument("--output-dir", type=Path, default=BENCHMARKS_DIR)
    parser.add_argument("--difficulty", action="append", choices=DIFFICULTIES, default=[], help="Difficulty to generate; repeatable. Defaults to easy, medium, hard.")
    parser.add_argument("--attempts", type=int, default=2, help="Generation attempts per difficulty.")
    parser.add_argument("--timeout", type=int, default=900, help="LLM timeout per difficulty in seconds.")
    parser.add_argument("--test-timeout", type=int, default=120, help="pytest timeout when --verify-gold-fails is enabled.")
    parser.add_argument("--verify-gold-fails", action="store_true", help="Run generated hidden tests against the gold-master and require normal pytest failures.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing benchmark directories.")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep the temporary clone used for generation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    env_file = read_env_file(ROOT / ".env")
    args = build_parser().parse_args(argv)
    args.model = resolve_model_override(args.runner, args.model, env_file)
    if not args.repo:
        print("Missing target repo. Pass --repo or set EVAL_TARGET_REPO in .env.", file=sys.stderr)
        return 2
    if not args.sha:
        print("Missing gold-master SHA. Pass --sha or set EVAL_TARGET_SHA in .env.", file=sys.stderr)
        return 2
    difficulties = args.difficulty or list(DIFFICULTIES)

    temp: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_workspace:
        workspace_root = Path(tempfile.mkdtemp(prefix="awb_eval_seed_"))
    else:
        temp = tempfile.TemporaryDirectory(prefix="awb_eval_seed_")
        workspace_root = Path(temp.name)
    workspace = workspace_root / "workspace"

    try:
        print("Preparing target repo snapshot for benchmark generation")
        prepare_workspace(args.repo, args.sha, workspace)
        for difficulty in difficulties:
            generate_one(difficulty, workspace, args)
        print("Benchmark generation complete")
        return 0
    except BenchmarkGenerationError as exc:
        print(f"Benchmark generation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.keep_workspace:
            print(f"Kept generation workspace at {workspace}")
        elif temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
