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


@unittest.skipUnless(shutil.which("node"), "node is required for GUI telemetry regression tests")
class GuiTelemetryRegressionTests(unittest.TestCase):
    def test_easy__telemetry_chart_defaults_and_hooks_are_present(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('/static/vendor/echarts.min.js', html)
        self.assertIn('chartBucket:"day"', html)
        self.assertIn('rollup:"run"', html)
        self.assertIn('bucket:TELEMETRY_STATE.chartBucket || "day"', html)
        self.assertIn('api(`/telemetry/runs/${jobId}/profile`)', html)
        self.assertIn('function telemetryChartsAvailable()', html)

    def test_medium__pick_default_telemetry_run_prefers_active_then_newest(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "gui" / "index.html").read_text(encoding="utf-8")
        fn = _extract_function(html, "pickDefaultTelemetryRun")
        script = textwrap.dedent(
            f"""
            {fn}

            function assert(condition, message) {{
              if (!condition) throw new Error(message);
            }}

            const newestSucceeded = {{ id: "job_newest", status: "succeeded" }};
            const olderRunning = {{ id: "job_running", status: "running" }};
            const queued = {{ id: "job_queued", status: "queued" }};

            assert(pickDefaultTelemetryRun([]) === null, "empty run list should produce no default");
            assert(pickDefaultTelemetryRun([newestSucceeded, olderRunning]).id === "job_running", "running run should win over newer completed runs");
            assert(pickDefaultTelemetryRun([newestSucceeded, queued]).id === "job_queued", "queued run should win when no run is actively running");
            assert(pickDefaultTelemetryRun([newestSucceeded]).id === "job_newest", "newest visible run should be fallback when nothing is active");
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
