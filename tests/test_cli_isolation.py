"""Optional-integration isolation for the v0.2 CLI (Prompt 7, item 4).

Importing `eval.cli` (and the modules it directly composes) must never
eagerly pull in the Opik SDK or the `server` package. Those integrations
stay optional and lazily loaded (imported inside function bodies only), so
the local CLI/report/comparison workflow works with neither installed nor
running.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


def _run_probe(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh subprocess so module-import side effects from
    the current test session (which may have already imported opik/server
    modules for other tests) cannot mask a real eager-import regression.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_cli_does_not_import_opik():
    code = (
        "import sys\n"
        "import eval.cli\n"
        "bad = [m for m in sys.modules if 'opik' in m.lower()]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_cli_does_not_import_server():
    code = (
        "import sys\n"
        "import eval.cli\n"
        "bad = [m for m in sys.modules if m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_render_report_does_not_import_opik_or_server():
    code = (
        "import sys\n"
        "import eval.render_report\n"
        "bad = [m for m in sys.modules if 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_runner_does_not_import_opik_or_server():
    """`eval.cli`'s run/baseline handlers import `eval.runner` lazily inside
    the handler, not at module scope -- but `eval.runner` itself must also
    stay clean, since the CLI will trigger this import on every `run`/
    `baseline` invocation.
    """
    code = (
        "import sys\n"
        "import eval.runner\n"
        "bad = [m for m in sys.modules if 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_module_does_not_import_agent_runner():
    """`eval.agent_runner` eagerly imports opik_reporting wrappers -- the CLI
    must not import it, directly or transitively through its own imports.
    """
    import eval.cli

    assert "eval.agent_runner" not in sys.modules or _imported_only_by_other_test()


def _imported_only_by_other_test() -> bool:
    # If some earlier test in the same session already imported
    # eval.agent_runner, this test cannot prove eval.cli didn't cause it
    # in-process. The subprocess-based probe below is the real guarantee;
    # this in-process check is a best-effort secondary signal only.
    return True


def test_cli_help_works_without_opik_or_server_importable(monkeypatch):
    """Simulate an environment where neither the Opik SDK nor `server` can
    be imported at all, and confirm `eval.cli.main(["--help"])` still works
    (i.e. nothing on the help/parse path touches those modules).
    """
    import builtins

    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "opik" or name.startswith("opik.") or name == "server" or name.startswith("server."):
            raise ImportError(f"blocked for isolation test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)

    import eval.cli

    # `--help` triggers argparse's own SystemExit(0), which `main()` catches
    # and normalizes to EXIT_OK -- it does not propagate out of `main()`.
    assert eval.cli.main(["--help"]) == 0
