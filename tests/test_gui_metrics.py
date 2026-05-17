import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"Could not find {marker!r} in gui/index.html")
    brace_start = source.find("{", start)
    if brace_start == -1:
        raise AssertionError(f"Could not find opening brace for {name}")
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Could not find closing brace for {name}")


@unittest.skipUnless(shutil.which("node"), "node is required for GUI metric regression tests")
class GuiMetricsRegressionTests(unittest.TestCase):
    def test_medium__gui_metrics_helpers_accumulate_and_estimate_cumulative_values(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "gui" / "index.html").read_text(encoding="utf-8")
        functions = "\n\n".join(
            _extract_function(html, name)
            for name in (
                "formatMoney",
                "estimateDisplayedCostUsd",
                "resolveDisplayedCostUsd",
                "applyWorkflowEventToJob",
            )
        )
        script = textwrap.dedent(
            f"""
            {functions}

            function assert(condition, message) {{
              if (!condition) throw new Error(message);
            }}

            const accumulated = applyWorkflowEventToJob(
              {{ tokens_in: 98, tokens_out: 160, cost_usd: 0, status: "running" }},
              {{ type: "metrics", tokens_in: 1200, tokens_out: 800, cost_usd: 0 }}
            );
            assert(accumulated.tokens_in === 1298, "metrics events should accumulate input tokens");
            assert(accumulated.tokens_out === 960, "metrics events should accumulate output tokens");
            assert(accumulated.cost_usd === 0, "missing explicit cost should remain zero in job state");

            const estimated = resolveDisplayedCostUsd(accumulated);
            assert(estimated > 0, "display cost should fall back to a positive estimate when tokens exist");
            assert(formatMoney(estimated) === "$0.0183", "estimated display cost should use cumulative tokens when no explicit cost is present");
            assert(formatMoney(0.00005) === "<$0.0001", "small positive estimates should not round down to $0.0000");
            assert(resolveDisplayedCostUsd({{ tokens_in: 1, tokens_out: 1, cost_usd: 0.5 }}) === 0.5, "explicit cost should win over estimated display cost");
            """
        )
        subprocess.run(
            ["node", "-e", script],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
