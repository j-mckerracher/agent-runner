"""Import-isolation + cross-prompt regression tests for Prompt 5.

Covers coverage category 89-93: asserts the comparison layer never pulls in
server/Opik/telemetry/LLM modules, and that it composes cleanly with the
Prompt 2/3/4 contracts it depends on (`eval.report_schema`) without needing
`eval.benchmark_manifest` or `telemetry.*`.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

FORBIDDEN_MODULE_PREFIXES = ("opik", "server", "telemetry")

COMPARISON_MODULES = (
    "eval.comparison",
    "eval.classification",
    "eval.comparison_schema",
    "eval.comparison_policy",
    "eval.render_comparison",
)


class ImportIsolationTests(unittest.TestCase):
    def test_comparison_modules_do_not_import_server_or_opik_or_telemetry(self):
        # Run in a fresh subprocess so any modules already imported by the
        # pytest process itself (e.g. via other test files) can't mask a
        # real isolation violation.
        script = (
            "import sys\n"
            f"import {', '.join(COMPARISON_MODULES)}\n"
            "bad = [m for m in sys.modules if m.split('.')[0] in "
            f"{FORBIDDEN_MODULE_PREFIXES!r}]\n"
            "print(','.join(sorted(bad)))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        leaked = [m for m in proc.stdout.strip().split(",") if m]
        self.assertEqual(leaked, [], f"forbidden modules leaked into sys.modules: {leaked}")

    def test_comparison_engine_does_not_import_benchmark_manifest(self):
        script = (
            "import sys\n"
            "import eval.comparison\n"
            "print('eval.benchmark_manifest' in sys.modules)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")

    def test_comparison_module_imports_only_report_schema_from_eval(self):
        import ast

        tree = ast.parse((REPO_ROOT / "eval" / "comparison.py").read_text(encoding="utf-8"))
        eval_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("eval."):
                eval_imports.add(node.module)
        self.assertEqual(
            eval_imports,
            {"eval.classification", "eval.comparison_policy", "eval.comparison_schema", "eval.report_schema"},
        )


class CrossPromptRegressionTests(unittest.TestCase):
    """Confirms Prompt 5 didn't disturb the Prompt 2/3/4 contracts it sits on top of."""

    def test_report_schema_suite_still_passes(self):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_report_schema.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_benchmark_manifest_suite_still_passes(self):
        targets = [
            p.name
            for p in (REPO_ROOT / "tests").glob("test_benchmark_manifest*.py")
        ] + [
            p.name
            for p in (REPO_ROOT / "tests").glob("test_eval_seed_benchmarks*.py")
        ]
        if not targets:
            self.skipTest("no Prompt 3 benchmark manifest test files present")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *[f"tests/{t}" for t in targets]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_trace_contract_suite_still_passes(self):
        targets = []
        for pattern in ("test_trace_events*.py", "test_event_sink*.py", "test_trace_integration*.py"):
            targets.extend(p.name for p in (REPO_ROOT / "tests").glob(pattern))
        if not targets:
            self.skipTest("no Prompt 4 trace contract test files present")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *[f"tests/{t}" for t in targets]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
