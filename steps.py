from prefect import task, tags
from run_cmds import run_claude_cmd

# 1
@task(log_prints=True, name="intake")
def step_intake(ado_story_link: str, repo: str):
    with tags('intake-agent'):
        if not ado_story_link:
            raise ValueError("ADO story link cannot be empty.")
        print(f"Received ADO story link: {ado_story_link}")

        prompt = f"Intake the following story link: {ado_story_link}\n"
        prompt += f"Target repo: {repo}\n"
        prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
        prompt += "You MUST use the azure-devops-cli skill to interact with ADO"
        return run_claude_cmd(prompt=prompt, agent="intake-agent")

# 2
@task(log_prints=True, name="task-gen-producer")
def step_task_gen_producer(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-generator")


# 3
@task(log_prints=True, name="task-gen-evaluator")
def step_task_gen_evaluator(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-plan-evaluator")

# 4
@task(log_prints=True, name="task-assigner")
def step_task_assigner(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-assigner")

# 4b
@task(log_prints=True, name="assignment-evaluator")
def step_assignment_evaluator(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="assignment-evaluator")

# 5
@task(log_prints=True, name="software-engineer")
def step_software_engineer(uow_id: str, change_id: str, repo: str, evaluator_feedback: str = "") -> str:
    prompt = (
        f"Implement UoW {uow_id} for change {change_id}.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    if evaluator_feedback:
        prompt += (
            f"\n\n## Evaluator Issues to Fix:\n{evaluator_feedback}\n\n"
            f"Address every issue listed above. Do not ask questions — act immediately."
        )
    return run_claude_cmd(prompt=prompt, agent="software-engineer-hyperagent")

# 6
@task(log_prints=True, name="software-engineer-evaluator")
def step_software_engineer_evaluator(uow_id: str, change_id: str, repo: str) -> str:
    prompt = (
        f"Evaluate the implementation of UoW {uow_id} for change {change_id}.\n"
        f"Read the implementation report from agent-context/{change_id}/execution/{uow_id}/impl_report.yaml.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    return run_claude_cmd(prompt=prompt, agent="implementation-evaluator")

# 7
@task(log_prints=True, name="qa-engineer")
def step_qa_engineer(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="qa-engineer")

# 8
@task(log_prints=True, name="qa-evaluator")
def step_qa_evaluator(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="qa-evaluator")

# 9
@task(log_prints=True, name="lessons-optimizer")
def step_lessons_optimizer(change_id: str, repo: str) -> str:
    prompt = (
        f"Run the end-of-workflow lessons optimization for change {change_id}.\n"
        f"Read agent-context/lessons.md for recorded lessons.\n"
        f"Read all execution artifacts under agent-context/{change_id}/.\n"
        f"Target repo: {repo}\n"
        f"Write your report to agent-context/{change_id}/summary/lessons_optimizer_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    return run_claude_cmd(prompt=prompt, agent="lessons-optimizer-hyperagent")