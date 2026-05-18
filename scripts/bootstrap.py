"""Cross-platform bootstrapper for local agent-runner with optional self-hosted Opik."""
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
OPIK_INFO_URL = "https://github.com/comet-ml/opik/blob/main/README.md"
BOOTSTRAP_REEXEC_ENV = "AGENT_RUNNER_BOOTSTRAP_REEXEC"
DOCKER_READY_TIMEOUT_SECONDS = 90
DOCKER_PROBE_TIMEOUT_SECONDS = 15
DOCKER_PROBE_DELAY_SECONDS = 3


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


def _bootstrap_entrypoint() -> Path:
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and argv0 != "-c":
        return Path(argv0).resolve()
    return (RUNNER_ROOT / "bootstrap.py").resolve()


def _reexec_bootstrap(target_python: Path, env: dict[str, str]) -> None:
    cmd = [str(target_python), str(_bootstrap_entrypoint()), *sys.argv[1:]]
    if _is_windows():
        result = subprocess.run(cmd, env=env, text=True)
        raise SystemExit(result.returncode)
    os.execve(str(target_python), cmd, env)


def _ensure_virtualenv() -> None:
    venv_python = _venv_python_path()
    if not venv_python.exists():
        _echo_step("Creating local virtual environment")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])

    current_python = Path(sys.executable).resolve()
    target_python = venv_python.resolve()
    if current_python == target_python:
        print(f"[bootstrap] Virtual environment ready ({target_python})", flush=True)
        return
    if os.environ.get(BOOTSTRAP_REEXEC_ENV) == "1":
        raise BootstrapError(
            f"Bootstrap re-exec expected {target_python}, but still running under {current_python}"
        )

    _echo_step(f"Switching bootstrap to {target_python}")
    env = os.environ.copy()
    env[BOOTSTRAP_REEXEC_ENV] = "1"
    _reexec_bootstrap(target_python, env)


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


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if _is_windows():
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _docker_info_probe(timeout: int) -> None:
    proc = subprocess.Popen(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(proc)
        raise subprocess.TimeoutExpired(proc.args, timeout) from exc
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, proc.args)


def _check_docker() -> None:
    _echo_step("Checking Docker")
    _require_command("docker", install_hint="Install Docker Desktop and make sure it is running.")
    print(
        "[bootstrap] Waiting for Docker engine to respond "
        f"(up to {DOCKER_READY_TIMEOUT_SECONDS}s)...",
        flush=True,
    )
    last_error: Exception | None = None
    deadline = time.monotonic() + DOCKER_READY_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            _docker_info_probe(timeout=min(DOCKER_PROBE_TIMEOUT_SECONDS, max(1, int(remaining))))
            print("[bootstrap] Docker OK", flush=True)
            return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(min(DOCKER_PROBE_DELAY_SECONDS, max(0, deadline - time.monotonic())))

    if isinstance(last_error, subprocess.TimeoutExpired):
        raise BootstrapError(
            f"Docker did not respond after {DOCKER_READY_TIMEOUT_SECONDS} seconds. "
            "Docker Desktop may be open before the engine is ready; wait for it to finish starting "
            "or restart Docker Desktop and rerun bootstrap."
        ) from last_error
    raise BootstrapError(
        "Docker is installed but the engine is not ready yet. "
        "Wait for Docker Desktop to finish starting or restart it, then rerun bootstrap."
    ) from last_error


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
    for attempt, dashboard_url in enumerate(candidates, 1):
        api_url = f"{dashboard_url.rstrip('/')}/api"
        print(f"[bootstrap] Connecting to Opik at {api_url} (attempt {attempt}/{len(candidates)})...", flush=True)
        try:
            opik.configure(
                use_local=True,
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
            print(f"[bootstrap] Opik connected: {project_url}", flush=True)
            return settings
        except Exception as exc:  # noqa: BLE001 - bootstrap should surface concrete failure after retries.
            print(f"[bootstrap] Warning: could not connect to {api_url}: {exc}", flush=True)
            last_error = exc
            time.sleep(1)
    raise BootstrapError(f"Unable to configure the local Opik instance. Last error: {last_error}") from last_error


def _prompt_user_config() -> None:
    """Interactive prompts for first-time config values not set by other bootstrap steps."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    from server.config import load_config, save_config

    cfg = load_config()

    # --- Repo base directory ---
    current_base = (cfg.get("repo_paths", {}).get("base_dir") or "").strip()
    if not current_base:
        _echo_step("Repository base directory (optional)")
        print(
            "The Repo path dropdown in the UI shows subdirectories of a base directory.\n"
            "Enter the path to the directory that contains your local repositories.\n"
            "(Press Enter to skip — you can configure this later in the Settings panel.)",
            flush=True,
        )
        while True:
            try:
                raw = input("  Base directory [skip]: ").strip()
            except EOFError:
                print("[bootstrap] No input available; skipping.", flush=True)
                break
            if not raw:
                print("[bootstrap] Skipping — configure it later in Settings.", flush=True)
                break
            expanded = Path(os.path.expandvars(raw)).expanduser().resolve()
            if not expanded.is_dir():
                print(
                    f"[bootstrap] Warning: '{expanded}' does not exist or is not a directory. "
                    "Re-enter or press Enter to skip.",
                    flush=True,
                )
                continue
            save_config({"repo_paths": {"base_dir": str(expanded)}})
            print(f"[bootstrap] Repo base directory set to: {expanded}", flush=True)
            break

    # --- Default runner ---
    current_runner = (cfg.get("defaults", {}).get("runner") or "").strip()
    if not current_runner:
        _echo_step("Default runner")
        print(
            "Choose the default AI backend for workflow runs.\n"
            "Options: claude, copilot, gemini\n"
            "(Press Enter to use 'claude'.)",
            flush=True,
        )
        valid_runners = ("claude", "copilot", "gemini")
        try:
            raw = input(f"  Default runner [{valid_runners[0]}]: ").strip().lower()
        except EOFError:
            raw = ""
        runner = raw if raw in valid_runners else valid_runners[0]
        save_config({"defaults": {"runner": runner}})
        print(f"[bootstrap] Default runner set to: {runner}", flush=True)

    # --- Default model ---
    cfg = load_config()  # reload after possible runner change
    current_model = cfg.get("defaults", {}).get("model")
    if not current_model:
        from core.runner_models import RUNNER_MODEL_CHOICES, RUNNER_DEFAULT_MODELS

        runner = cfg.get("defaults", {}).get("runner", "claude")
        choices = list(RUNNER_MODEL_CHOICES.get(runner, ()))
        default_model = RUNNER_DEFAULT_MODELS.get(runner, "")
        if choices:
            _echo_step("Default model")
            print(
                f"Choose the default model for the '{runner}' runner.\n"
                f"Options: {', '.join(choices)}\n"
                f"(Press Enter to use '{default_model}'.)",
                flush=True,
            )
            try:
                raw = input(f"  Default model [{default_model}]: ").strip()
            except EOFError:
                raw = ""
            model = raw if raw in choices else default_model
            save_config({"defaults": {"model": model}})
            print(f"[bootstrap] Default model set to: {model}", flush=True)

    # --- Default mode ---
    current_mode = (cfg.get("defaults", {}).get("mode") or "").strip()
    if not current_mode:
        _echo_step("Default mode")
        print(
            "Choose the default execution mode.\n"
            "Options: live, hermetic\n"
            "(Press Enter to use 'live'.)",
            flush=True,
        )
        try:
            raw = input("  Default mode [live]: ").strip().lower()
        except EOFError:
            raw = ""
        mode = raw if raw in ("live", "hermetic") else "live"
        save_config({"defaults": {"mode": mode}})
        print(f"[bootstrap] Default mode set to: {mode}", flush=True)


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


def _server_env(opik_settings: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    opik_keys = (
        "OPIK_BASE_URL",
        "OPIK_DASHBOARD_URL",
        "OPIK_PROJECT_ID",
        "OPIK_PROJECT_NAME",
        "OPIK_URL_OVERRIDE",
        "OPIK_WORKSPACE",
    )
    if opik_settings is None:
        for key in opik_keys:
            env.pop(key, None)
        return env
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


def _prompt_for_opik() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    _echo_step("Optional: Opik observability")
    print(
        "Opik is an open-source LLM observability / evaluation platform. "
        "Bootstrap can clone its repo and start a local self-hosted stack "
        "(requires Docker Desktop running).\n"
        f"  Learn more: {OPIK_INFO_URL}\n"
        "Skip this to run the agent-runner without Opik (you can enable it later).",
        flush=True,
    )
    try:
        raw = input("  Enable Opik now? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return raw in ("y", "yes")


def _start_local_opik(opik_dir: Path) -> dict[str, str]:
    _echo_step("Starting local self-hosted Opik")
    result = _run(_opik_start_command(opik_dir), cwd=opik_dir, capture_output=True)
    candidates = _candidate_dashboard_urls(result.stdout)
    _echo_step("Configuring local Opik client settings")
    return _configure_local_opik(candidates, project_name=DEFAULT_OPIK_PROJECT_NAME)


def _start_server(*, host: str, port: int, reload: bool, opik_settings: dict[str, str] | None) -> None:
    _echo_step("Starting agent-runner server")
    print(f"[bootstrap] agent-runner UI: http://{host}:{port}", flush=True)
    if opik_settings is not None:
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
        description="Install agent-runner dependencies and run the local server. Optionally start a local Opik stack (--with-opik)."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Server bind host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Server bind port (default: {DEFAULT_PORT})")
    parser.add_argument("--reload", action="store_true", help="Start the FastAPI server with --reload.")
    opik_group = parser.add_mutually_exclusive_group()
    opik_group.add_argument(
        "--with-opik",
        action="store_true",
        help="Enable the bundled local Opik stack (requires Docker). Skips the interactive prompt.",
    )
    opik_group.add_argument(
        "--no-opik",
        action="store_true",
        help="Skip the bundled local Opik stack. Skips the interactive prompt.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _echo_step("agent-runner bootstrap")
        print(f"[bootstrap] Python: {sys.executable}", flush=True)
        print(f"[bootstrap] Root:   {RUNNER_ROOT}", flush=True)
        _ensure_virtualenv()
        _warn_if_no_ai_backend()
        _check_ztk()
        _install_requirements()
        _materialize_agents()
        _prompt_user_config()

        if args.with_opik:
            enable_opik = True
        elif args.no_opik:
            enable_opik = False
        else:
            enable_opik = _prompt_for_opik()

        if enable_opik:
            _check_docker()
            opik_dir = _opik_repo_dir()
            _sync_opik_repo(opik_dir)
            opik_settings = _start_local_opik(opik_dir)
            _save_opik_config(opik_settings)
        else:
            opik_settings = None
            print("[bootstrap] Opik disabled. Re-run with --with-opik to enable later.", flush=True)


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
