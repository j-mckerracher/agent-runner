import subprocess
from prefect import flow, task, tags
from datetime import datetime

# ====================== HELPERS ====================== #

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")

# ====================== STEPS ====================== #

# 1
@task(log_prints=True)
def step_intake(ado_story_link: str, repo: str):
    with tags('intake-agent'):
        if not ado_story_link:
            raise ValueError("ADO story link cannot be empty.")
        print(f"Received ADO story link: {ado_story_link}")

        prompt = f"Intake the following story link: {ado_story_link}\n"
        prompt += f"Target repo: {repo}\n"
        prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
        prompt += "You MUST use the azure-devops-cli skill to interact with ADO"
        run_claude(prompt=prompt)

# 2
@task(log_prints=True)
def step_task_gen_producer(context: str) -> None:
    pass

# 3
@task(log_prints=True)
def step_task_gen_evaluator(context: str) -> None:
    pass

# ====================== EVAL-OPTIMIZER LOOP ====================== #

@flow(log_prints=True, timeout_seconds=1800)
def run_eval_optimizer_loop(producer_func, evaluator_func, iter_count: int = 3):
    for _ in range(iter_count):
        producer_func()
        evaluator_func()

# ====================== RUN COMMANDS ============================= #

@task(log_prints=True)
def run_claude(
    prompt: str,
    agent: str = "intake-agent",
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """
    Trigger Claude Code via the CLI.

    Args:
        prompt: The prompt or ADO URL to pass with -p.
        agent: The --agent value.
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

@flow(log_prints=True, timeout_seconds=3600)
def main():
    print("Welcome to Agent Runner!")
    ado_url = "https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/5035632"
    repo = "/Users/mckerracher.joshua/Code/mcs-products-mono-ui"
    # ado_url = input("Please enter your ADO (Azure DevOps) URL: ").strip()
    print(f"Got it! Using ADO URL: {ado_url}")
    step_intake(ado_story_link=ado_url, repo=repo)
    return ado_url

if __name__ == "__main__":
    with tags(f"Running Claude Code {get_time()}"):
        main()

