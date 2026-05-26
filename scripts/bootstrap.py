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

from core.env_file import read_env_file

RUNNER_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = RUNNER_ROOT / ".venv"
ENV_FILE = RUNNER_ROOT / ".env"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8742
DEFAULT_OPIK_DASHBOARD_URL = "http://localhost:5173"
DEFAULT_OPIK_PROJECT_NAME = "agent-runner"
DEFAULT_OPIK_REPO_URL = "https://github.com/comet-ml/opik.git"
OPIK_INFO_URL = "https://github.com/comet-ml/opik/blob/main/README.md"
RTK_REPO_URL = "https://dev.azure.com/mclm/Mayo%20Open%20Developer%20Network/_git/mayo-rtk-ai"
RTK_TAG = "mayo-v0.39.0"
RTK_INSTALL_INFO_URL = "https://dev.azure.com/mclm/Mayo%20Open%20Developer%20Network/_git/mayo-rtk-ai"
BOOTSTRAP_REEXEC_ENV = "AGENT_RUNNER_BOOTSTRAP_REEXEC"
OPIK_RUNTIME_ENV_KEYS = (
    "OPIK_BASE_URL",
    "OPIK_DASHBOARD_URL",
    "OPIK_PROJECT_ID",
    "OPIK_PROJECT_NAME",
    "OPIK_URL_OVERRIDE",
    "OPIK_WORKSPACE",
)


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


def _running_in_runner_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def _bootstrap_entrypoint() -> Path:
    return (RUNNER_ROOT / "bootstrap.py").resolve()


def _ensure_virtualenv() -> None:
    venv_python = _venv_python_path()
    if not venv_python.exists():
        _echo_step("Creating local virtual environment")
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])

    if _running_in_runner_venv():
        return
    if os.environ.get(BOOTSTRAP_REEXEC_ENV) == "1":
        raise BootstrapError(
            f"Bootstrap re-exec expected virtualenv {VENV_DIR}, but still running under {sys.executable}"
        )

    entrypoint = _bootstrap_entrypoint()
    _echo_step(f"Switching bootstrap to {venv_python}")
    env = os.environ.copy()
    env[BOOTSTRAP_REEXEC_ENV] = "1"
    os.execve(str(venv_python), [str(venv_python), str(entrypoint), *sys.argv[1:]], env)


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
        "at least one of: claude, copilot, gemini, or an openai-compat-based runner.",
        flush=True,
    )


def _announce_optional_azure_devops() -> None:
    print(
        "[bootstrap] Azure DevOps integration is optional. You can use Agent Workbench manually by "
        "pasting story details in the UI, then enable Azure DevOps CLI or MCP later in Settings if needed.",
        flush=True,
    )


def _register_rtk_global_permission() -> None:
    """Add Bash(rtk *) to ~/.claude/settings.json permissions.allow if not already present."""
    import json as _json

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        settings: dict = _json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except Exception as exc:
        print(f"[bootstrap] Warning: could not read {settings_path}: {exc}", flush=True)
        return

    allow: list = settings.setdefault("permissions", {}).setdefault("allow", [])
    if "Bash(rtk *)" not in allow:
        allow.append("Bash(rtk *)")
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(_json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            print(f"[bootstrap] Added Bash(rtk *) to {settings_path}", flush=True)
        except Exception as exc:
            print(f"[bootstrap] Warning: could not write {settings_path}: {exc}", flush=True)
    else:
        print(f"[bootstrap] Bash(rtk *) already present in {settings_path}", flush=True)


def _install_rtk() -> bool:
    """Build and install rtk from the Mayo ADO repo. Returns True on success."""
    _echo_step("Installing rtk (token-optimized CLI proxy)")

    if not _find_command("cargo"):
        print(
            "[bootstrap] Warning: Rust toolchain not found (cargo missing). rtk install skipped.\n"
            "  Install Rust: https://rustup.rs\n"
            "  Then install rtk manually: see " + RTK_INSTALL_INFO_URL,
            flush=True,
        )
        return False

    git_cmd = _find_command("git")
    if not git_cmd:
        print("[bootstrap] Warning: git not found. rtk install skipped.", flush=True)
        return False

    build_dir = RUNNER_ROOT / ".rtk-build"
    try:
        if build_dir.exists():
            _run(["rm", "-rf", str(build_dir)])
        _run([git_cmd, "clone", "--branch", RTK_TAG, "--depth", "1", RTK_REPO_URL, str(build_dir)])
        _run(["cargo", "build", "--release"], cwd=build_dir)

        if _is_windows():
            dest_dir = Path.home() / ".cargo" / "bin"
            dest_dir.mkdir(parents=True, exist_ok=True)
            src = build_dir / "target" / "release" / "rtk.exe"
            shutil.copy2(str(src), str(dest_dir / "rtk.exe"))
        else:
            dest_dir = Path.home() / ".local" / "bin"
            dest_dir.mkdir(parents=True, exist_ok=True)
            src = build_dir / "target" / "release" / "rtk"
            shutil.copy2(str(src), str(dest_dir / "rtk"))
            if sys.platform == "darwin":
                subprocess.run(
                    ["codesign", "-s", "-", str(dest_dir / "rtk")],
                    check=False, capture_output=True,
                )

        print(f"[bootstrap] rtk installed to {dest_dir}", flush=True)
        return True
    except BootstrapError as exc:
        print(f"[bootstrap] Warning: rtk build/install failed: {exc}", flush=True)
        return False
    finally:
        if build_dir.exists():
            _run(["rm", "-rf", str(build_dir)])


def _check_rtk() -> None:
    if not _find_command("rtk"):
        if not _install_rtk():
            print(
                "[bootstrap] Warning: rtk not available. Token compression will be disabled.\n"
                "  See: " + RTK_INSTALL_INFO_URL,
                flush=True,
            )
            return
    print("[bootstrap] rtk found — running rtk init -g to register global Claude Code hook.", flush=True)
    try:
        subprocess.run(["rtk", "init", "-g"], check=True, capture_output=True, text=True)
        print("[bootstrap] rtk init -g completed.", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[bootstrap] Warning: rtk init -g failed: {exc.stderr or exc}. Hook may not be registered.", flush=True)
    print("[bootstrap] Running rtk init -g --gemini to register global Gemini hook.", flush=True)
    try:
        subprocess.run(["rtk", "init", "-g", "--gemini"], check=True, capture_output=True, text=True)
        subprocess.run(["rtk", "init", "-g", "--copilot"], check=True, capture_output=True, text=True)
        subprocess.run(["rtk", "init", "-g", "--codex"], check=True, capture_output=True, text=True)
        print("[bootstrap] rtk init -g --gemini completed.", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[bootstrap] Warning: rtk init failed: {exc.stderr or exc}. The hook may not be registered.", flush=True)
    _register_rtk_global_permission()


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
            return settings
        except Exception as exc:  # noqa: BLE001 - bootstrap should surface concrete failure after retries.
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
            "Options: claude, copilot, gemini, openai-compat\n"
            "(Press Enter to use 'claude'.)",
            flush=True,
        )
        valid_runners = ("claude", "copilot", "gemini", "openai-compat")
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


def _read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    return read_env_file(path)


def _quote_env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+\-=]+", value):
        return value
    return json_dumps(value)


def json_dumps(value: str) -> str:
    import json as _json

    return _json.dumps(value)


def _write_env_values(values: dict[str, str]) -> None:
    cleaned = {key: str(value).strip() for key, value in values.items() if str(value).strip()}
    if not cleaned:
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _old = stripped.split("=", 1)
        key = key.strip()
        if key in cleaned:
            out.append(f"{key}={_quote_env_value(cleaned[key])}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in cleaned.items():
        if key not in seen:
            out.append(f"{key}={_quote_env_value(value)}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _git_head_sha(repo: str) -> str | None:
    if not repo:
        return None
    path = Path(os.path.expandvars(repo)).expanduser()
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _default_eval_runner() -> str:
    env_values = _read_env_file()
    if env_values.get("EVAL_RUNNER"):
        return env_values["EVAL_RUNNER"]
    try:
        from server.config import load_config

        configured = (load_config().get("defaults", {}).get("runner") or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    return "claude"


def _prompt_yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {prompt} {suffix}: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in {"y", "yes", "true", "1"}


def _collect_eval_config(args: argparse.Namespace) -> dict[str, str | bool]:
    """Collect optional eval bootstrap settings and persist .env values."""
    env_values = _read_env_file()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    repo = (args.eval_target_repo or env_values.get("EVAL_TARGET_REPO") or "").strip()
    sha = (args.eval_target_sha or env_values.get("EVAL_TARGET_SHA") or "").strip()
    runner = (args.eval_runner or env_values.get("EVAL_RUNNER") or _default_eval_runner()).strip()
    model = (args.eval_model or env_values.get("EVAL_MODEL") or "").strip()

    generate = bool(args.generate_eval_benchmarks)
    if args.skip_eval_benchmarks:
        generate = False
    elif not generate and interactive:
        _echo_step("Optional: workflow eval benchmarks")
        print(
            "Bootstrap can use your configured LLM CLI to generate three local eval benchmarks "
            "(easy, medium, hard) for a target repo. These are saved under ignored eval/benchmarks/.",
            flush=True,
        )
        generate = _prompt_yes_no("Generate eval benchmarks now?", default=False)

    if generate:
        if interactive and not repo:
            try:
                repo = input("  Eval target repo path or Git URL: ").strip()
            except EOFError:
                repo = ""
        if not repo:
            raise BootstrapError("Eval benchmark generation requires --eval-target-repo or interactive repo input.")

        default_sha = sha or _git_head_sha(repo) or ""
        if interactive and not sha:
            prompt = f"  Gold-master commit SHA [{default_sha or 'required'}]: "
            try:
                raw_sha = input(prompt).strip()
            except EOFError:
                raw_sha = ""
            sha = raw_sha or default_sha
        elif not sha:
            sha = default_sha
        if not sha:
            raise BootstrapError("Eval benchmark generation requires --eval-target-sha. Could not infer HEAD from target repo.")

        if interactive and not args.eval_runner:
            try:
                raw_runner = input(f"  Eval generator runner [{runner}]: ").strip()
            except EOFError:
                raw_runner = ""
            runner = raw_runner or runner
        if interactive and not args.eval_model:
            try:
                raw_model = input("  Eval generator model [runner default]: ").strip()
            except EOFError:
                raw_model = ""
            model = raw_model or model

    elif repo or sha or args.eval_runner or args.eval_model:
        if not sha:
            sha = _git_head_sha(repo) or sha

    values = {
        "EVAL_TARGET_REPO": repo,
        "EVAL_TARGET_SHA": sha,
        "EVAL_RUNNER": runner,
    }
    if model:
        values["EVAL_MODEL"] = model
    if repo or sha or args.eval_runner or args.eval_model or generate:
        _write_env_values(values)
        print(f"[bootstrap] Eval config saved to {ENV_FILE}", flush=True)

    return {
        "generate": generate,
        "repo": repo,
        "sha": sha,
        "runner": runner,
        "model": model,
    }


def _generate_eval_benchmarks(config: dict[str, str | bool], args: argparse.Namespace) -> None:
    if not config.get("generate"):
        return
    _echo_step("Generating eval benchmarks")
    cmd: list[object] = [
        sys.executable,
        str(RUNNER_ROOT / "eval" / "seed_benchmarks.py"),
        "--repo",
        str(config["repo"]),
        "--sha",
        str(config["sha"]),
        "--runner",
        str(config["runner"]),
    ]
    if config.get("model"):
        cmd.extend(["--model", str(config["model"])])
    if args.force_eval_benchmarks:
        cmd.append("--force")
    if getattr(args, "no_verify_eval_gold_fails", False):
        cmd.append("--no-verify-gold-fails")
    _run(cmd, cwd=RUNNER_ROOT)


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
    for key in OPIK_RUNTIME_ENV_KEYS:
        env.pop(key, None)
    if opik_settings is None:
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
        str(RUNNER_ROOT / "server_main.py"),
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
    parser.add_argument("--materialize", action="store_true", help="Manually materialize enabled runner assets during bootstrap. Off by default so prompt-file changes remain explicit.")
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
    parser.add_argument("--eval-target-repo", default=None, help="Target repo path or Git URL used for generated workflow eval benchmarks.")
    parser.add_argument("--eval-target-sha", default=None, help="Gold-master commit SHA for generated workflow eval benchmarks.")
    eval_group = parser.add_mutually_exclusive_group()
    eval_group.add_argument("--generate-eval-benchmarks", action="store_true", help="Use an LLM to generate eval/benchmarks/{easy,medium,hard} during bootstrap.")
    eval_group.add_argument("--skip-eval-benchmarks", action="store_true", help="Do not prompt for or generate eval benchmarks during bootstrap.")
    parser.add_argument("--eval-runner", default=None, help="LLM for benchmark generation: claude, copilot, copilot-* alias, gemini, or openai-compat. Defaults to configured runner.")
    parser.add_argument("--eval-model", default=None, help="Optional model override for benchmark generation.")
    parser.add_argument("--force-eval-benchmarks", action="store_true", help="Overwrite existing generated benchmark folders.")
    parser.add_argument(
        "--no-verify-eval-gold-fails",
        action="store_true",
        help="Do not run generated hidden tests against gold-master during benchmark generation. Intended only for local debugging.",
    )
    parser.add_argument("--verify-eval-gold-fails", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        _ensure_virtualenv()
        _warn_if_no_ai_backend()
        _announce_optional_azure_devops()
        _check_rtk()
        _install_requirements()
        if getattr(args, "materialize", False):
            _materialize_agents()
        else:
            print("[bootstrap] Skipping agent/skill materialization by default. Re-run with --materialize to update generated runner assets.", flush=True)
        _prompt_user_config()
        eval_config = _collect_eval_config(args)
        _generate_eval_benchmarks(eval_config, args)

        if getattr(args, "with_opik", False):
            enable_opik = True
        elif getattr(args, "no_opik", False):
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
