import argparse
import subprocess
from prefect import task, tags
from datetime import datetime

# ====================== HELPERS ====================== #

SUPPORTED_AGENTS = [
    "reference-librarian",
    "information-explorer",
]

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke a Claude agent (reference-librarian or information-explorer) via the CLI."
    )
    parser.add_argument(
        "--prompt", "-p",
        required=True,
        help="The prompt to pass to the agent.",
    )
    parser.add_argument(
        "--agent", "-a",
        default="reference-librarian",
        choices=SUPPORTED_AGENTS,
        help="Target agent to invoke. Defaults to 'reference-librarian'.",
    )
    parser.add_argument(
        "--model", "-m",
        default="claude-haiku-4-5-20251001",
        help="Claude model to use.",
    )
    parser.add_argument(
        "--no-skip-permissions",
        action="store_true",
        help="Disable --dangerously-skip-permissions (enabled by default).",
    )
    parser.add_argument(
        "--extra-flags",
        nargs=argparse.REMAINDER,
        help="Any additional raw CLI flags to forward to Claude.",
    )
    return parser.parse_args()

# ====================== TASK ====================== #

@task(log_prints=True)
def run_claude(
    prompt: str,
    agent: str = "reference-librarian",
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """
    Trigger Claude Code via the CLI.

    Args:
        prompt: The prompt to pass with -p.
        agent: The --agent value ('reference-librarian' or 'information-explorer').
        model: The --model value.
        skip_permissions: Whether to include --dangerously-skip-permissions.
        extra_flags: Any additional CLI flags to append.

    Returns:
        The CompletedProcess result from subprocess.
    """
    print(f"Starting Claude Code via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["claude", "-p", prompt, "--agent", agent, "--model", model]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result

# ====================== MAIN ====================== #

if __name__ == "__main__":
    args = parse_args()
    with tags(f"Invoking {args.agent} Agent {get_time()}"):
        run_claude(
            prompt=args.prompt,
            agent=args.agent,
            model=args.model,
            skip_permissions=not args.no_skip_permissions,
            extra_flags=args.extra_flags or None,
        )
