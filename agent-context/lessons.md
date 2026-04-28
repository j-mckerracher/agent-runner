# Workflow Lessons

This file stores cross-run lessons captured by the workflow.

- No lessons recorded yet for synthetic local workflow testing.
- lesson_id: "SE-EXEC-REPO-MISMATCH-BLOCK"
  target_agents: ["software-engineer-hyperagent"]
  stage_tags: ["execution"]
  trigger_context: ["missing uow spec", "repo mismatch", "blocked execution"]
  mistake_pattern: "Execution attempted to proceed or hand off without first converting a missing execution spec and target-repo mismatch into a concrete blocked artifact set."
  prevention_rule: "Before touching code, verify that `execution/{UOW-ID}/uow_spec.yaml` exists and that `intake/config.yaml.code_repo` contains at least one implementation surface that matches the story scope. If either check fails, stop code changes and emit a blocked impl_report with filesystem evidence."
  trigger_check: "Check for `execution/{UOW-ID}/uow_spec.yaml` and one repo-local file or symbol family that maps to the acceptance-criteria technology/runtime."
  agent: "software-engineer-hyperagent"
  timestamp: "2026-04-28T18:02:06Z"
