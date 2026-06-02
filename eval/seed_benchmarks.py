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
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_paths import eval_benchmarks_root, load_data_dir_override_from_env_file

load_data_dir_override_from_env_file(ROOT / ".env")
from core.runner_models import RUNNER_MODEL_CHOICES, is_copilot_runner

BENCHMARKS_DIR = eval_benchmarks_root()
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

    if runner == "openai-compat":
        return inherited_model
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
        "easy": """
EASY benchmark requirements:
- The story must require a small, localized change in one obvious area.
- Prefer a pure function, formatter, parser, validator, config default, CLI flag, or simple UI utility.
- Hidden tests must validate behavior through the public/exported API when possible.
- Do not require an exact implementation shape. For example, do not require `export function foo`
  if `export const foo = ...` would be equally correct.
- Include at least:
  - one positive behavior test;
  - one edge-case test;
  - one regression test proving existing behavior is preserved.
""".strip(),
        "medium": """
MEDIUM benchmark requirements:
- The story must require a coordinated change across a few files or one integration seam.
- Prefer behavior involving validators, adapters, serialization/deserialization, persistence boundaries,
  API/CLI request handling, config loading, or component-to-helper integration.
- Hidden tests should execute the relevant runtime path when feasible.
- Source-text inspection is allowed only as a secondary guard, never as the only evidence,
  unless the repository cannot execute the relevant language locally.
- Include at least:
  - allowed-input behavior;
  - rejected-input behavior;
  - regression behavior for nearby cases that must not change;
  - one check that the old bug pattern is absent if the task is about correcting a known bad pattern.
""".strip(),
        "hard": """
HARD benchmark requirements:
- The story must require a bounded cross-cutting change across multiple modules.
- Prefer behavior involving error handling, permissions, classification, state transitions,
  workflow orchestration, caching, retry logic, or compatibility behavior.
- Hidden tests must primarily test externally observable behavior at a service/API/module boundary.
- Avoid tests that merely check for string literals, class names, or enum names unless those checks
  are supplemental to behavioral assertions.
- Include at least:
  - the new intended behavior;
  - at least two regression cases that must remain unchanged;
  - a negative case proving the new behavior is not over-applied;
  - an edge case around missing/empty/unknown input.
""".strip(),
    }[difficulty]

    return f"""
You are creating one high-quality hidden-test benchmark for Agent Workbench.

You are currently inside a temporary clone of the target repository at gold-master commit {target_sha}.
Inspect the repository before choosing the benchmark. Do not modify files. Do not ask questions.

Create exactly one {difficulty.upper()} benchmark.

{difficulty_guidance}

Avoid the known weak benchmark patterns:
- Do not write tests that pass after merely finding an expected string in a file.
- Do not write tests that require one exact function declaration or one exact code layout.
- Do not write tests that silently skip behavioral assertions when a runtime is unavailable.
- For utility/function tasks, import or invoke the exported symbol and assert outputs.
- For validator/parser tasks, run valid and invalid examples through the real validator/parser path.
- For error-classification or service tasks, instantiate or call the service/classification boundary and
  assert returned category, severity, display behavior, dismissibility, and regression behavior.

The purpose of this benchmark is to evaluate whether an autonomous coding workflow can satisfy
a real work item according to acceptance criteria, while preserving nearby existing behavior.

Mandatory benchmark requirements:
1. The story must describe a realistic user-facing or maintainer-facing work item.
2. Every acceptance criterion must be objectively testable.
3. Every acceptance criterion must be covered by at least one hidden test.
4. Hidden tests must fail against the current gold-master commit for the intended behavioral reason.
5. Hidden tests must pass after a correct, semantically equivalent implementation.
6. Hidden tests must be deterministic, fast, local, and safe.
7. Hidden tests must not use the network, credentials, paid APIs, browser automation,
   external services, long-running daemons, destructive operations, or real databases.
8. Hidden tests must not call pytest.skip, pytest.xfail, unittest.skip, or conditionally skip.
9. If a required local runtime or dependency is unavailable, the test must fail with a clear assertion
   message explaining the missing local command. Do not skip.
10. Prefer public behavior over brittle implementation details.
11. Do not overfit to one exact implementation. Accept alternate correct implementations.
12. Do not create tasks that require large migrations, broad product design choices, new services,
    authentication setup, or external infrastructure.
13. Do not create tests that pass merely because a file contains a string.
14. If source inspection is unavoidable, combine it with behavioral tests where possible and explain
    the intent in comments.

Hidden test design rules:
- Name tests using the acceptance criterion number, for example:
  `test_ac1_truncates_long_values`.
- Define an `AC_TEST_MAP` dictionary at the top of hidden_tests.py mapping each AC id to test names.
- Each test should have a short comment explaining the user-observable behavior it validates.
- Use clear assertion messages that explain what failed.
- Include both positive and negative cases.
- Include regression cases for behavior that should not change.
- Keep tests independent; no test should depend on another test's side effects.
- Use temporary files/directories for filesystem tests.
- Use subprocess only for local CLI/runtime execution.
- If invoking JavaScript/TypeScript code, prefer an existing local project command or checked-in runtime path.
  Do not assume globally installed tools. Check local `node_modules/.bin` first when appropriate.
- If invoking Python code, import modules directly when possible and set PYTHONPATH to the repository root
  inside the test if needed.
- Do not require exact function declarations, exact whitespace, exact import ordering, or exact private names
  unless the acceptance criterion is explicitly about that public contract.

The generated story must include acceptance criteria with stable IDs:
- AC1, AC2, AC3, etc.
- Each acceptance criterion string must start with its ID, for example `AC1: Observable requirement.`
- Include at least 3 acceptance criteria.
- Mark critical criteria in metadata.

Return exactly these two blocks and no other prose:

<story.json>
{{
  "change_id": "EVAL-{difficulty.upper()}-001",
  "title": "Concise work-item title",
  "description": "User-story style description of the requested change.",
  "acceptance_criteria": [
    "AC1: Observable requirement.",
    "AC2: Regression requirement.",
    "AC3: Edge-case or negative-case requirement."
  ],
  "metadata": {{
    "difficulty": "{difficulty}",
    "target_sha": "{target_sha}",
    "generated": true,
    "critical_acceptance_criteria": ["AC1"],
    "benchmark_contract": {{
      "primary_signal": "behavioral hidden pytest tests",
      "no_hidden_test_skips": true,
      "requires_gold_master_failure": true,
      "accepts_semantically_equivalent_implementations": true
    }}
  }}
}}
</story.json>

<hidden_tests.py>
# Pytest tests only.
# This file will be copied to the target repo root and run with:
# python -m pytest hidden_tests.py --disable-warnings -q
#
# Required structure:
# AC_TEST_MAP = {{
#     "AC1": ["test_ac1_example"],
#     "AC2": ["test_ac2_example"],
#     "AC3": ["test_ac3_example"],
# }}
#
# Do not use pytest.skip, pytest.xfail, unittest.skip, network calls, credentials,
# browser automation, external services, destructive operations, or long-running processes.
</hidden_tests.py>
""".strip()


def build_repair_prompt(
    *,
    difficulty: str,
    target_sha: str,
    story_json: str,
    hidden_tests_py: str,
    failure_reason: str | None = None,
) -> str:
    failure_section = (
        f"""
The previous benchmark failed validation or gold-master verification for this reason:
{failure_reason}
""".strip()
        if failure_reason
        else "The previous benchmark needs to be strengthened before it can be accepted."
    )
    return f"""
You are improving an existing Agent Workbench eval benchmark.

You are currently inside a temporary clone of the target repository at gold-master commit {target_sha}.
Inspect the repository. Do not modify files. Do not ask questions.

Your task is to rewrite the benchmark so it is a stronger evaluation of acceptance-criteria satisfaction.

Current difficulty: {difficulty}

{failure_section}

Current story.json:
```json
{story_json}
```

Current hidden_tests.py:
```python
{hidden_tests_py}
```

Required improvements:
1. Preserve the same general work item intent unless the current task is fundamentally invalid.
2. Make hidden tests primarily behavioral rather than source-string based.
3. Remove all pytest.skip, pytest.xfail, unittest.skip, and conditional skip behavior.
4. Hidden tests must fail against gold master for the intended behavioral reason.
5. Hidden tests must pass after a correct implementation.
6. Add or preserve stable AC ids: AC1, AC2, AC3, etc.
7. Define AC_TEST_MAP in hidden_tests.py mapping AC ids to test function names.
8. Avoid requiring one exact implementation shape.
9. Add positive, negative, edge-case, and regression coverage.
10. If source inspection is unavoidable, keep it supplemental and explain why in a comment.
11. Keep tests deterministic, fast, local, and safe.
12. Do not use network calls, credentials, browser automation, paid APIs, external services,
    destructive operations, or long-running services.

Difficulty-specific expectations:
- Easy: public/exported behavior, no exact syntax requirement, at least one edge case.
- Medium: execute the validator/adapter/API/CLI path if feasible; source checks only as backup.
- Hard: instantiate or invoke the service/module boundary and assert actual classification/state/output behavior.

Return exactly these two blocks and no other prose:

<story.json>
{{
  "change_id": "EVAL-{difficulty.upper()}-001",
  "title": "Concise work-item title",
  "description": "User-story style description of the requested change.",
  "acceptance_criteria": [
    "AC1: Observable requirement.",
    "AC2: Regression requirement.",
    "AC3: Edge-case or negative-case requirement."
  ],
  "metadata": {{
    "difficulty": "{difficulty}",
    "target_sha": "{target_sha}",
    "generated": true,
    "repaired": true,
    "critical_acceptance_criteria": ["AC1"],
    "benchmark_contract": {{
      "primary_signal": "behavioral hidden pytest tests",
      "no_hidden_test_skips": true,
      "requires_gold_master_failure": true,
      "accepts_semantically_equivalent_implementations": true
    }}
  }}
}}
</story.json>

<hidden_tests.py>
# Pytest tests only.
# This file will be copied to the target repo root and run with:
# python -m pytest hidden_tests.py --disable-warnings -q
#
# Required:
# - Define AC_TEST_MAP.
# - Use test names beginning with test_ac1_, test_ac2_, etc.
# - Do not skip or xfail.
# - Prefer behavior over source inspection.
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


def _read_and_remove_output(path: Path, fallback: str) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return fallback
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


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
    elif runner == "codex":
        if not shutil.which("codex"):
            raise BenchmarkGenerationError("codex CLI was not found on PATH")
        output_path = Path(tempfile.gettempdir()) / f"agent-workbench-codex-benchmark-{os.getpid()}-{time.time_ns()}.txt"
        cmd = [
            "codex",
            "exec",
            "--cd",
            str(cwd),
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
        ]
        if model:
            cmd += ["--model", model]
        cmd.append(prompt)
    elif runner == "openai-compat":
        from core.run_cmds import run_openai_compat_text
        effective_model = model or "gemma4:31b-cloud"
        print(f"[openai-compat] Invoking benchmark generation with model={effective_model}")
        openai_result = run_openai_compat_text(
            prompt=prompt,
            model=effective_model,
            runner="openai-compat",
        )
        return openai_result.strip()
    else:
        raise BenchmarkGenerationError(
            f"Unsupported benchmark generator runner {runner!r}. Use claude, codex, copilot, a copilot-* alias, gemini, or openai-compat."
        )

    result = run_cmd(cmd, cwd=cwd, timeout=timeout, env=env)
    if result.returncode != 0:
        raise BenchmarkGenerationError(
            f"{runner} benchmark generation failed with exit code {result.returncode}:\n{tail(result.stderr or result.stdout)}"
        )
    if runner == "codex":
        return _read_and_remove_output(output_path, result.stdout).strip()
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
    if len(ac) < 3:
        raise BenchmarkGenerationError("acceptance_criteria must include at least 3 AC-prefixed requirements")
    ac_ids = acceptance_criterion_ids(ac)
    if len(ac_ids) != len(ac):
        raise BenchmarkGenerationError("each acceptance criterion must start with a stable AC id, e.g. AC1: Observable requirement")
    if len(set(ac_ids)) != len(ac_ids):
        raise BenchmarkGenerationError("acceptance_criteria must not contain duplicate AC ids")
    metadata = story.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        story["metadata"] = metadata
    metadata.setdefault("difficulty", difficulty)
    metadata.setdefault("target_sha", target_sha)
    metadata.setdefault("generated", True)
    return story


def acceptance_criterion_ids(acceptance_criteria: list[str]) -> list[str]:
    ids: list[str] = []
    for item in acceptance_criteria:
        match = re.match(r"^\s*(AC[1-9][0-9]*)\s*:", item)
        if match:
            ids.append(match.group(1))
    return ids


def dotted_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return None


def is_forbidden_skip_or_xfail(name: str | None) -> bool:
    if not name:
        return False
    return (
        name in {"pytest.skip", "pytest.xfail", "pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}
        or name.startswith("unittest.skip")
        or name == "unittest.SkipTest"
        or name in {"skip", "skipIf", "skipUnless", "SkipTest", "xfail"}
        or name in {"mark.skip", "mark.skipif", "mark.xfail"}
    )


def test_function_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }


def find_ac_test_map(tree: ast.Module) -> dict[str, list[str]]:
    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "AC_TEST_MAP" for target in node.targets):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "AC_TEST_MAP":
            value = node.value
        if value is None:
            continue
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise BenchmarkGenerationError("AC_TEST_MAP must be a literal dictionary mapping AC ids to test-name lists") from exc
        if not isinstance(parsed, dict):
            raise BenchmarkGenerationError("AC_TEST_MAP must be a dictionary mapping AC ids to test-name lists")
        ac_map: dict[str, list[str]] = {}
        for key, names in parsed.items():
            if not isinstance(key, str) or not re.fullmatch(r"AC[1-9][0-9]*", key):
                raise BenchmarkGenerationError("AC_TEST_MAP keys must be stable AC ids such as AC1")
            if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
                raise BenchmarkGenerationError("AC_TEST_MAP values must be non-empty lists of test function names")
            ac_map[key] = names
        return ac_map
    raise BenchmarkGenerationError("hidden_tests.py must define AC_TEST_MAP mapping AC ids to test names")


def validate_hidden_tests(hidden_tests: str) -> str:
    content = hidden_tests.strip() + "\n"
    if "def test_" not in content:
        raise BenchmarkGenerationError("hidden_tests.py must define at least one pytest test function named test_*")
    try:
        tree = ast.parse(content, filename="hidden_tests.py")
    except SyntaxError as exc:
        raise BenchmarkGenerationError(f"hidden_tests.py has invalid Python syntax: {exc}") from exc
    if not isinstance(tree, ast.Module):
        raise BenchmarkGenerationError("hidden_tests.py must parse as a Python module")

    tests = test_function_names(tree)
    if not tests:
        raise BenchmarkGenerationError("hidden_tests.py must define at least one pytest test function named test_*")
    if not any(re.match(r"test_ac[1-9][0-9]*(_|$)", name) for name in tests):
        raise BenchmarkGenerationError("hidden_tests.py test names should include AC ids, e.g. test_ac1_handles_edge_case")

    find_ac_test_map(tree)

    blocked_modules = ("socket", "requests", "httpx", "aiohttp", "ftplib", "paramiko")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []

        for name in names:
            if any(name == blocked or name.startswith(blocked + ".") for blocked in blocked_modules):
                raise BenchmarkGenerationError(
                    f"hidden_tests.py imports blocked network/external module {name!r}; generated tests must be local and deterministic"
                )
            if name == "pytest" and isinstance(node, ast.ImportFrom) and any(alias.name in {"skip", "xfail"} for alias in node.names):
                raise BenchmarkGenerationError("hidden_tests.py must not import pytest skip or xfail helpers")
            if name == "unittest" and isinstance(node, ast.ImportFrom) and any(
                alias.name in {"skip", "skipIf", "skipUnless", "SkipTest"} for alias in node.names
            ):
                raise BenchmarkGenerationError("hidden_tests.py must not import unittest skip helpers")

        if isinstance(node, ast.Call) and is_forbidden_skip_or_xfail(dotted_name(node.func)):
            raise BenchmarkGenerationError("hidden_tests.py must not use pytest/unittest skip or xfail behavior")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if is_forbidden_skip_or_xfail(dotted_name(decorator)):
                    raise BenchmarkGenerationError("hidden_tests.py must not use pytest/unittest skip or xfail decorators")
        if isinstance(node, ast.Raise) and is_forbidden_skip_or_xfail(dotted_name(node.exc)):
            raise BenchmarkGenerationError("hidden_tests.py must not raise unittest.SkipTest")
    return content


def validate_ac_test_map(story: dict[str, Any], hidden_tests: str) -> None:
    tree = ast.parse(hidden_tests, filename="hidden_tests.py")
    tests = test_function_names(tree)
    ac_map = find_ac_test_map(tree)
    required_ids = set(acceptance_criterion_ids(story["acceptance_criteria"]))
    missing_ids = sorted(required_ids - set(ac_map))
    if missing_ids:
        raise BenchmarkGenerationError(f"AC_TEST_MAP is missing acceptance criteria: {', '.join(missing_ids)}")
    unknown_tests = sorted({name for names in ac_map.values() for name in names if name not in tests})
    if unknown_tests:
        raise BenchmarkGenerationError(f"AC_TEST_MAP references missing test functions: {', '.join(unknown_tests)}")


def parse_benchmark_response(text: str, *, difficulty: str, target_sha: str) -> tuple[dict[str, Any], str]:
    story_text = extract_block(text, "story.json")
    tests_text = extract_block(text, "hidden_tests.py")
    story = validate_story(story_text, difficulty=difficulty, target_sha=target_sha)
    hidden_tests = validate_hidden_tests(tests_text)
    validate_ac_test_map(story, hidden_tests)
    return story, hidden_tests


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
        output = ""
        try:
            output = invoke_llm(
                runner=args.runner,
                prompt=prompt,
                cwd=workspace,
                model=args.model,
                timeout=args.timeout,
            )
            story, hidden_tests = parse_benchmark_response(output, difficulty=difficulty, target_sha=args.sha)
            if not args.no_verify_gold_fails:
                verify_gold_fails(workspace, hidden_tests, args.test_timeout)
            write_benchmark(args.output_dir / difficulty, story, hidden_tests, force=args.force)
            print(f"Wrote {args.output_dir / difficulty}")
            return
        except Exception as exc:  # noqa: BLE001 - show retryable parse/generation details.
            last_error = exc
            print(f"Generation attempt failed for {difficulty}: {exc}", file=sys.stderr)
            try:
                prompt = build_repair_prompt(
                    difficulty=difficulty,
                    target_sha=args.sha,
                    story_json=extract_block(output, "story.json"),
                    hidden_tests_py=extract_block(output, "hidden_tests.py"),
                    failure_reason=str(exc),
                )
            except BenchmarkGenerationError:
                prompt = build_prompt(difficulty=difficulty, target_sha=args.sha)
    raise BenchmarkGenerationError(f"Unable to generate {difficulty} benchmark after {args.attempts} attempt(s): {last_error}")


def build_parser() -> argparse.ArgumentParser:
    env_file = read_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Seed eval/benchmarks/{easy,medium,hard} with LLM-generated stories and hidden tests.")
    parser.add_argument("--repo", default=default("EVAL_TARGET_REPO", env_file), help="Target Git repo path or URL. Defaults to EVAL_TARGET_REPO.")
    parser.add_argument("--sha", default=default("EVAL_TARGET_SHA", env_file), help="Gold-master commit SHA. Defaults to EVAL_TARGET_SHA.")
    parser.add_argument("--runner", default=default("EVAL_RUNNER", env_file, "claude"), help="LLM to use: claude, codex, copilot, copilot-* alias, gemini, or openai-compat.")
    parser.add_argument("--model", default=None, help="Optional model override for the selected runner.")
    parser.add_argument("--output-dir", type=Path, default=BENCHMARKS_DIR)
    parser.add_argument("--difficulty", action="append", choices=DIFFICULTIES, default=[], help="Difficulty to generate; repeatable. Defaults to easy, medium, hard.")
    parser.add_argument("--attempts", type=int, default=2, help="Generation attempts per difficulty.")
    parser.add_argument("--timeout", type=int, default=900, help="LLM timeout per difficulty in seconds.")
    parser.add_argument("--test-timeout", type=int, default=120, help="pytest timeout when checking generated hidden tests against gold-master.")
    parser.add_argument(
        "--no-verify-gold-fails",
        action="store_true",
        help="Do not run generated hidden tests against gold-master. Intended only for local debugging.",
    )
    parser.add_argument("--verify-gold-fails", action="store_true", help=argparse.SUPPRESS)
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
