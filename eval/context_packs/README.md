# Agent Eval Context Packs

Context packs are versioned prompt-adjacent guidance used by
`eval/agent_runner.py`. They let you compare prompt text separately from the
runtime context supplied to an agent.

Use `baseline` for prompt-only comparisons, then try focused packs such as
`schema-examples-v1`, `ac-checklist-v1`, or `scoped-repo-facts-v1` when a failure
cluster suggests the agent needs more structure.

A context pack should be small, explicit, and reproducible. Avoid raw repository
dumps or large historical logs; prefer schema reminders, AC checklists, one good
example, and scoped repository facts.
