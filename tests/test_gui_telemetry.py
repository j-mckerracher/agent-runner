import re
import shutil
import subprocess
import textwrap
import unittest

from gui_sources import read_gui_markup, read_gui_scripts, read_gui_sources


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"Could not find {marker!r} in GUI scripts")
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
                return source[start : index + 1]
    raise AssertionError(f"Could not find closing brace for {name}")


@unittest.skipUnless(shutil.which("node"), "node is required for GUI telemetry regression tests")
class GuiTelemetryRegressionTests(unittest.TestCase):
    def test_easy__telemetry_chart_defaults_and_hooks_are_present(self) -> None:
        html = read_gui_sources()
        markup = read_gui_markup()
        compact_html = re.sub(r"\s+", " ", html)

        self.assertIn("/static/vendor/echarts.min.js", markup)
        self.assertRegex(html, re.compile(r'<option value="all" selected>\s*All time\s*</option>'))
        self.assertRegex(html, re.compile(r'chartBucket:\s*"day"'))
        self.assertRegex(html, re.compile(r'rollup:\s*"run"'))
        self.assertIn('const range = $("#telemetry-range")?.value || "all";', html)
        self.assertIn('$("#telemetry-range").value = "all";', html)
        self.assertRegex(html, re.compile(r'bucket:\s*TELEMETRY_STATE\.chartBucket \|\| "day"'))
        self.assertIn('api(`/telemetry/runs/${jobId}/profile`)', html)
        self.assertIn("function telemetryChartsAvailable()", html)
        self.assertIn("Stage Token Use", compact_html)
        self.assertIn('id="telemetry-chart-stage-token-boxplot"', html)
        self.assertIn("payload.stage_token_boxplot || []", html)
        self.assertIn("Model Comparison by Average Token Usage Per Run", compact_html)
        self.assertIn("Model Comparison by Average Time Per Run", compact_html)
        self.assertIn('name: "Average tokens per run"', html)
        self.assertIn('name: "Average time per run"', html)
        self.assertIn("function formatCompactNumber(value)", html)
        self.assertIn("formatCompactNumber(params?.value)", html)
        self.assertRegex(
            html,
            re.compile(
                r'"telemetry-chart-models",\s*\{\s*grid:\s*\{\s*left:\s*172,\s*right:\s*78,\s*top:\s*34,\s*bottom:\s*64,\s*containLabel:\s*false',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r'name:\s*"Tokens/run",\s*nameLocation:\s*"middle",\s*nameGap:\s*38,\s*axisLabel:\s*\{\s*formatter:\s*\(value\)\s*=>\s*formatCompactNumber\(value\)',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r'"telemetry-chart-model-counts",\s*\{\s*grid:\s*\{\s*left:\s*146,\s*right:\s*42,\s*top:\s*34,\s*bottom:\s*64,\s*containLabel:\s*false',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r'name:\s*"Average time/run",\s*nameLocation:\s*"middle",\s*nameGap:\s*38',
                re.DOTALL,
            ),
        )
        self.assertRegex(html, re.compile(r'type:\s*"log",\s*name:\s*"Total tokens"'))
        self.assertRegex(html, re.compile(r'type:\s*"log",\s*name:\s*"Tokens/run"'))
        self.assertRegex(html, re.compile(r'nameTextStyle:\s*\{\s*color:\s*"#e2ddd5",\s*fontWeight:\s*700'))

    def test_medium__pick_default_telemetry_run_prefers_active_then_newest(self) -> None:
        source = read_gui_scripts()
        fn = _extract_function(source, "pickDefaultTelemetryRun")
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
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
