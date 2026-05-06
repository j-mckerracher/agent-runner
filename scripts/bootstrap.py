"""Cross-platform bootstrapper for local agent-runner + self-hosted Opik."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

RUNNER_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = RUNNER_ROOT / ".venv"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8742
DEFAULT_OPIK_DASHBOARD_URL = "http://localhost:5173"
DEFAULT_OPIK_PROJECT_NAME = "agent-runner"
DEFAULT_OPIK_REPO_URL = "https://github.com/comet-ml/opik.git"
BOOTSTRAP_REEXEC_ENV = "AGENT_RUNNER_BOOTSTRAP_REEXEC"


class BootstrapError(RuntimeError):
    """Raised when bootstrap cannot proceed safely."""


def _is_windows() -> bool:
    return os.name == "nt"


def _echo_step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def _shell_join(parts: Iterable[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _run(
    cmd: list[object],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
    echo: bool = True,
) -> subprocess.CompletedProcess[str]:
    args = [str(part) for part in cmd]
    if echo:
        print(f"$ {_shell_join(args)}", flush=True)
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=capture_output,
    )
    if capture_output:
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="", flush=True)
    if result.returncode != 0:
        raise BootstrapError(
            f"Command failed with exit code {result.returncode}: {_shell_join(args)}"
        )
    return result


def _venv_python_path() -> Path:
    if _is_windows():
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _ensure_virtualenv() -> None:
    venv_python = _venv_python_path()
    if not venv_python.exists():
        _echo_step("Creating local virtual environment")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])

    current_python = Path(sys.executable).resolve()
    target_python = venv_python.resolve()
    if current_python == target_python:
        return
    if os.environ.get(BOOTSTRAP_REEXEC_ENV) == "1":
        raise BootstrapError(
            f"Bootstrap re-exec expected {target_python}, but still running under {current_python}"
        )

    _echo_step(f"Switching bootstrap to {target_python}")
    env = os.environ.copy()
    env[BOOTSTRAP_REEXEC_ENV] = "1"
    os.execve(str(target_python), [str(target_python), str(__file__), *sys.argv[1:]], env)


def _find_command(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _require_command(*names: str, install_hint: str) -> str:
    found = _find_command(*names)
    if found:
        return found
    rendered = ", ".join(names)
    raise BootstrapError(f"Missing required command ({rendered}). {install_hint}")


def _warn_if_no_ai_backend() -> None:
    available = {
        name: path
        for name, path in (
            ("claude", _find_command("claude")),
            ("copilot", _find_command("copilot")),
            ("gemini", _find_command("gemini")),
        )
        if path
    }
    if available:
        names = ", ".join(sorted(available))
        print(f"[bootstrap] Detected AI backend CLI(s): {names}", flush=True)
        return
    print(
        "[bootstrap] Warning: no AI backend CLI was detected. "
        "The local server will start, but workflow runs will fail until you install and authenticate "
        "at least one of: claude, copilot, gemini.",
        flush=True,
    )


def _register_ztk_global_permission() -> None:
    """Add Bash(ztk *) to ~/.claude/settings.json permissions.allow if not already present."""
    import json as _json

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        settings: dict = _json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except Exception as exc:
        print(f"[bootstrap] Warning: could not read {settings_path}: {exc}", flush=True)
        return

    allow: list = settings.setdefault("permissions", {}).setdefault("allow", [])
    if "Bash(ztk *)" not in allow:
        allow.append("Bash(ztk *)")
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(_json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            print(f"[bootstrap] Added Bash(ztk *) to {settings_path}", flush=True)
        except Exception as exc:
            print(f"[bootstrap] Warning: could not write {settings_path}: {exc}", flush=True)
    else:
        print(f"[bootstrap] Bash(ztk *) already present in {settings_path}", flush=True)


def _check_ztk() -> None:
    if not _find_command("ztk"):
        print(
            "[bootstrap] Warning: ztk not found. Token compression will be disabled for the claude runner.\n"
            "  Install: brew install codejunkie99/ztk/ztk\n"
            "  See: https://github.com/codejunkie99/ztk",
            flush=True,
        )
        return
    print("[bootstrap] ztk found — running ztk init -g to register global Claude Code hook.", flush=True)
    try:
        subprocess.run(["ztk", "init", "-g"], check=True, capture_output=True, text=True)
        print("[bootstrap] ztk init -g completed.", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[bootstrap] Warning: ztk init -g failed: {exc.stderr or exc}. Hook may not be registered.", flush=True)
    _register_ztk_global_permission()


def _check_docker() -> None:
    _require_command("docker", install_hint="Install Docker Desktop and make sure it is running.")
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BootstrapError("Docker is not running or not accessible. Start Docker Desktop first.") from exc


def _install_requirements() -> None:
    _echo_step("Installing Python dependencies")
    _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    _run([sys.executable, "-m", "pip", "install", "-r", str(RUNNER_ROOT / "requirements.txt")])


def _materialize_agents() -> None:
    _echo_step("Materializing agent prompts")
    _run([sys.executable, str(RUNNER_ROOT / "core" / "materialize.py")])


def _opik_repo_dir() -> Path:
    from server.paths import data_dir

    return data_dir() / "opik"


def _sync_opik_repo(opik_dir: Path) -> None:
    git_cmd = _require_command("git", install_hint="Install git so bootstrap can fetch the local Opik repo.")
    if opik_dir.exists():
        if not (opik_dir / ".git").exists():
            raise BootstrapError(f"Expected a git checkout at {opik_dir}, but no .git directory was found.")
        _echo_step("Updating local Opik checkout")
        _run([git_cmd, "-C", str(opik_dir), "pull", "--ff-only"])
        return

    _echo_step("Cloning the local Opik stack")
    opik_dir.parent.mkdir(parents=True, exist_ok=True)
    _run([git_cmd, "clone", "--depth", "1", DEFAULT_OPIK_REPO_URL, str(opik_dir)])


def _opik_start_command(opik_dir: Path) -> list[str]:
    if _is_windows():
        powershell = _require_command(
            "pwsh",
            "powershell",
            "powershell.exe",
            install_hint="PowerShell is required to start the local Opik stack on Windows.",
        )
        return [powershell, "-ExecutionPolicy", "Bypass", "-File", str(opik_dir / "opik.ps1")]
    return ["bash", str(opik_dir / "opik.sh")]


def _candidate_dashboard_urls(output: str) -> list[str]:
    found = re.findall(r"https?://localhost:\d+", output or "")
    ordered: list[str] = []
    for candidate in [*found, DEFAULT_OPIK_DASHBOARD_URL, "http://localhost:5174"]:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _parse_opik_project_url(project_url: str) -> dict[str, str]:
    parsed = urlparse(project_url)
    path = parsed.path.rstrip("/")
    match = re.search(r"/workspaceGuard/([^/]+)/projects/([^/]+)$", path)
    if not match:
        raise BootstrapError(f"Unexpected Opik project URL shape: {project_url}")
    return {
        "dashboard_url": f"{parsed.scheme}://{parsed.netloc}",
        "workspace_name": unquote(match.group(1)),
        "project_id": unquote(match.group(2)),
    }


def _configure_local_opik(candidates: list[str], *, project_name: str) -> dict[str, str]:
    import opik

    last_error: Exception | None = None
    for dashboard_url in candidates:
        api_url = f"{dashboard_url.rstrip('/')}/api"
        try:
            opik.configure(
                url=api_url,
                url_override=api_url,
                project_name=project_name,
                force=True,
            )
            client = opik.Opik(project_name=project_name)
            client.auth_check()
            trace = client.trace(
                name="agent-runner-bootstrap",
                input={"source": "bootstrap"},
                output={"status": "ok"},
                metadata={"bootstrap": True},
                thread_id="bootstrap",
                project_name=project_name,
            )
            trace.end()
            client.flush()
            project_url = client.get_project_url(project_name=project_name)
            settings = _parse_opik_project_url(project_url)
            settings["api_url"] = api_url
            settings["project_name"] = project_name
            settings["project_url"] = project_url
            return settings
        except Exception as exc:  # noqa: BLE001 - bootstrap should surface concrete failure after retries.
            last_error = exc
            time.sleep(1)
    raise BootstrapError(f"Unable to configure the local Opik instance. Last error: {last_error}") from last_error


def _save_opik_config(opik_settings: dict[str, str]) -> dict:
    from server.config import load_config, save_config, validate_config

    payload = {
        "opik": {
            "dashboard_url": opik_settings["dashboard_url"],
            "workspace_name": opik_settings["workspace_name"],
            "project_id": opik_settings["project_id"],
            "project_name": opik_settings["project_name"],
        }
    }
    merged = load_config()
    merged.setdefault("opik", {}).update(payload["opik"])
    errors = validate_config(merged)
    if errors:
        raise BootstrapError(f"Persisted Opik config is invalid: {errors}")
    return save_config(payload)


def _server_env(opik_settings: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OPIK_BASE_URL": opik_settings["api_url"],
            "OPIK_DASHBOARD_URL": opik_settings["dashboard_url"],
            "OPIK_PROJECT_ID": opik_settings["project_id"],
            "OPIK_PROJECT_NAME": opik_settings["project_name"],
            "OPIK_URL_OVERRIDE": opik_settings["api_url"],
            "OPIK_WORKSPACE": opik_settings["workspace_name"],
        }
    )
    return env


def _start_local_opik(opik_dir: Path) -> dict[str, str]:
    _echo_step("Starting local self-hosted Opik")
    result = _run(_opik_start_command(opik_dir), cwd=opik_dir, capture_output=True)
    candidates = _candidate_dashboard_urls(result.stdout)
    _echo_step("Configuring local Opik client settings")
    return _configure_local_opik(candidates, project_name=DEFAULT_OPIK_PROJECT_NAME)


def _start_server(*, host: str, port: int, reload: bool, opik_settings: dict[str, str]) -> None:
    _echo_step("Starting agent-runner server")
    print(f"[bootstrap] agent-runner UI: http://{host}:{port}", flush=True)
    print(f"[bootstrap] local Opik UI: {opik_settings['dashboard_url']}", flush=True)
    cmd: list[object] = [
        sys.executable,
        str(RUNNER_ROOT / "server" / "main.py"),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        cmd.append("--reload")
    _run(cmd, cwd=RUNNER_ROOT, env=_server_env(opik_settings), echo=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install agent-runner dependencies, start a local Opik stack, and run the local server."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Server bind host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Server bind port (default: {DEFAULT_PORT})")
    parser.add_argument("--reload", action="store_true", help="Start the FastAPI server with --reload.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _ensure_virtualenv()
        _check_docker()
        _warn_if_no_ai_backend()
        _check_ztk()
        _install_requirements()
        _materialize_agents()
        opik_dir = _opik_repo_dir()
        _sync_opik_repo(opik_dir)
        opik_settings = _start_local_opik(opik_dir)
        _save_opik_config(opik_settings)
        _start_server(host=args.host, port=args.port, reload=args.reload, opik_settings=opik_settings)
        return 0
    except KeyboardInterrupt:
        print("\n[bootstrap] Interrupted.", flush=True)
        return 130
    except BootstrapError as exc:
        print(f"\n[bootstrap] Error: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
