## PR-468504 graceful shutdown planning findings

**Date**: 2026-04-28
**Source**: task_generator_findings for workflow change PR-468504

The planning findings for PR-468504 conclude that the task plan should follow the explicit .NET API acceptance criteria instead of the runner-supplied `mcs-products-mono-ui` repository path. Available evidence confirmed that the mono-ui workspace does not contain the named APIs, so planning against that workspace would fabricate scope.

The resulting task plan uses four broad phases:

1. Host-lifecycle graceful shutdown wiring across `rls-orders-cnsmr-api` and `rls-docgen-system-api`
2. Deep cancellation propagation in `rls-docgen-system-api` to the same depth described by reference PR `468504`
3. Equivalent shutdown-aware cancellation work for `rls-orders-cnsmr-api`
4. Documentation of out-of-scope follow-up APIs and the repo-mismatch assumption

The findings also record that `rls-orders-orch-api` and `rls-orders-data-api` remain out-of-scope follow-up APIs, and that concrete implementation evidence was only available for the `rls-docgen-system-api` PR `468504` propagation chain.

Relevant workflow artifacts:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/planning/tasks.yaml`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/intake/story.yaml`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/intake/constraints.md`

## PR-468504 UOW-003 execution blockage findings

**Date**: 2026-04-28
**Source**: verified execution-stage artifacts for PR-468504 / UOW-003

Execution-stage evidence confirms that the authoritative execution artifact path remains `agent-context/PR-468504/execution/UOW-003/uow_spec.yaml`, and that file is missing. The existing planning artifacts `planning/tasks.yaml` and `planning/assignments.json` consistently map UOW-003 to task `T3` (`rls-orders-cnsmr-api` shutdown-aware cancellation), but they do not replace the missing execution specification and therefore are not authoritative execution inputs.

The blocked implementation report also confirms that the configured code repository `/Users/mckerracher.joshua/Code/mcs-products-mono-ui` should not be modified for this unit of work: no relevant `.csproj`, `.sln`, or `Program.cs` surfaces were found for the scoped .NET API work. The correct execution response for this condition was a blocked `impl_report.yaml` with replan guidance, plus an Azure DevOps blocker comment requesting the actual API repo and regenerated execution spec before retrying UOW-003.

Relevant workflow artifacts:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-003/impl_report.yaml`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/planning/tasks.yaml`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/planning/assignments.json`

## PR-468504 graceful shutdown planning findings (rls-orders-cnsmr-api scoped refinement)

**Date**: 2026-04-28
**Source**: task-generator findings for PR-468504 / WI-5043919 scoped to `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api`

These findings refine the graceful shutdown planning record to keep the work explicitly scoped to `rls-orders-cnsmr-api`. The dependency-safe broad plan is:

1. Host-lifecycle graceful shutdown and Serilog flush wiring
2. Deep `CancellationToken` propagation through core orders/tests request paths
3. Extension of the same shutdown-aware cancellation pattern to secondary endpoint chains
4. Explicit documentation of out-of-scope follow-up APIs and residual shutdown risks

The safe execution sequence is `T1 -> T2 -> T3 -> T4`.

Repo exploration identified `Program.cs` and `GoogleCloudLoggingStartup.cs` as the main bootstrap and shutdown surfaces. It also identified `OrdersController` and `TestsController`, through `OrderService`, helpers, and repositories, as the primary analogue chain for docgen-style deep cancellation propagation.

Scope must remain limited to `rls-orders-cnsmr-api`; `rls-orders-orch-api` and `rls-orders-data-api` should remain documented follow-up scope only.

Important planning knowledge gaps remain:

- Cloud Run termination budget is still unspecified
- Shared HTTP and storage abstractions may or may not support `CancellationToken`
- The final strategy for stopping acceptance of new work during shutdown is still undecided

Relevant workflow context:

- Original query context: broad task planning for graceful shutdown work in `rls-orders-cnsmr-api`
- Traceability: PR-468504 / WI-5043919

## PR-468504 planning/tasks.yaml schema compatibility findings

**Date**: 2026-04-28
**Source**: task_generator_findings for PR-468504 planning artifact revision

The repository currently has a schema mismatch for `planning/tasks.yaml`. The task-generator prompt prescribes `task_id`, `acceptance_criteria_mapped`, and `estimated_complexity`, while `.claude/scripts/validate-artifact-schema.py` validates `id`, `ac_mapping`, and `complexity`.

For PR-468504 iteration 2, the planning artifact was revised to carry both naming sets on each task entry for compatibility. Validation passed after those compatibility aliases were added.

This is a repo-level planning and evaluation inconsistency worth preserving because it can affect task generation, evaluator expectations, and schema validation until the prompt and validator contract are unified.

Relevant workflow artifacts:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-sources/task-generator/v1/prompt.md`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/.claude/scripts/validate-artifact-schema.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/planning/tasks.yaml`

## PR-468504 UOW-001 implementation blockage and baseline verification findings

**Date**: 2026-04-28
**Source**: software-engineer implementation-stage report-back for PR-468504 / UOW-001

Implementation for `PR-468504 / UOW-001` was blocked before any source-code changes were made. The authoritative execution artifact `agent-context/PR-468504/execution/UOW-001/uow_spec.yaml` was missing, and the implementation report records that `execution/` was empty when the software-engineer stage began. Because the scoped lesson `SE-EXEC-REPO-MISMATCH-BLOCK` applied, the engineer correctly emitted a blocked `impl_report.yaml` instead of treating planning artifacts as a substitute execution contract.

Baseline verification was still performed against `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api` to establish pre-change health. `dotnet build src/Mayo.MCS.RLS.OrdersConsumer.sln` succeeded, while `dotnet test src/Mayo.MCS.RLS.OrdersConsumer.sln` failed before any implementation work with pre-existing integration failures. The failing suite was `Mayo.MCS.RLS.OrdersConsumer.Tests.Integration.dll`, which reported `427 failed, 0 passed, 2 skipped`, dominated by `NullReferenceException` from `Mayo.ODN.Test.E2E.ConfigurationHelper.get_UseAzureADAuth()`. The unit suite passed with `1206 passed` and `3 skipped`.

Traceability from the implementation-stage report also records Azure DevOps work item `5043919` being updated to `Active` at implementation start, followed by a blocked-status discussion update after the missing execution spec was confirmed.

Relevant workflow artifacts:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-001/impl_report.yaml`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-001/logs/20260428_202120_session.json`

## PR-468504 UOW-002 cancellation-pattern baseline findings

**Date**: 2026-04-28
**Source**: software-engineer findings for PR-468504 / UOW-002

In `rls-orders-cnsmr-api`, no existing `CancellationToken` propagation or cooperative cancellation pattern currently exists under `src/`. The currently identified request-path entry points, `OrdersController` and `TestsController`, invoke downstream services without token parameters, so planned `PR-468504 / UOW-002` work would introduce the repository's first such pattern once the missing execution spec is materialized.

This is durable repo-shape knowledge and should be kept separate from the already-known generic missing-`uow_spec` blocker. The useful persistent fact is the current absence of any in-repo cancellation-propagation precedent in the core API request paths.

Relevant file paths:

- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/OrdersController.cs`
- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/TestsController.cs`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-002/impl_report.yaml`

## PR-468504 UOW-003 secondary-controller execution blocker findings

**Date**: 2026-04-28
**Source**: software_engineer_findings for PR-468504 / UOW-003

Execution for `PR-468504 / UOW-003` was blocked because the authoritative execution artifact `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-003/uow_spec.yaml` was missing. Updated repo verification shows this blocker should be treated as workflow artifact state, not as a repository-target mismatch.

## SQLite run_kind schema migration guidance for GET /runs failure

**Date**: 2026-04-30
**Source**: software_engineer_findings

Existing SQLite databases failed before migration because `_SCHEMA` created `idx_jobs_run_kind` during `executescript`, but legacy `jobs` tables did not yet have the `run_kind` column.

The durable fix pattern is:

1. Keep `run_kind` in the `jobs` table definition for fresh databases.
2. Remove `idx_jobs_run_kind` creation from `_SCHEMA` so legacy databases are not indexed before migration.
3. Let `server/db.py:_ensure_schema()` add `run_kind` for legacy databases and create the index only after the column exists.

Regression coverage now precreates a legacy `jobs.db` without `run_kind` and verifies `db.list_jobs(run_kind="regular")` migrates successfully.

Validation after the fix passed with `python3 -m pytest -q tests/` => `265 passed, 1 warning`.

Relevant file paths:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/server/db.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/tests/test_server_routes.py`

The intended secondary controller family touchpoints are present in `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api`:

- `AccountsController.cs`
- `DocumentsController.cs`
- `LabelsController.cs`
- `ReportsController.cs`

This means future `PR-468504 / UOW-003` retries should first confirm that `execution/UOW-003/uow_spec.yaml` has been regenerated under the artifact root, then use `planning/assignments.json` plus the secondary controller files above as the validation baseline for implementation scope. The blocked software-engineer report at `execution/UOW-003/impl_report.yaml` and the Azure DevOps blocker update on work item `5043919` are traceability evidence, not substitutes for the missing execution spec.

Relevant artifact and repository touchpoints:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/planning/assignments.json`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/PR-468504/execution/UOW-003/impl_report.yaml`
- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/AccountsController.cs`
- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/DocumentsController.cs`
- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/LabelsController.cs`
- `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api/src/Mayo.MCS.RLS.OrdersConsumer.Api/Controllers/ReportsController.cs`

## WI-EVAL-003-A failed run pre-event failure and Runs UI log gap

**Date**: 2026-04-29
**Source**: investigation findings for failed run `job_fd5cff592ed7f6c1` (`original_query`: Find the root cause of failed run `job_fd5cff592ed7f6c1` and why logs did not populate in the Runs UI.)

The failed submitted run used change_id `WI-EVAL-003-A` with synthetic story file `eval/stories/EVAL-003.json`, whose embedded fixture `change_id` is `EVAL-003`. `workflow_inputs.resolve_workflow_input()` raises `ValueError` on that mismatch before `run.py` emits any events or enters its main stage try/except, so the subprocess exits with `rc=1`, the job is persisted as `failed`, `events.jsonl` stays empty, and `jobs.error_message` remains null for this failure mode.

The Runs UI renders terminal output only from replayed/streamed events fetched through `/runs/{job_id}/events` and `/runs/{job_id}/stream`. Because this failure happens before any event emission, the terminal panel has nothing to display. Separately, `server/runner_proc.py` captures the subprocess traceback through stdout/stderr, drains that pipe, and discards it, while the current UI does not render the database `error_message`, so the underlying traceback is not surfaced anywhere in the Runs view.

Evidence was reproduced locally by calling `resolve_workflow_input()` with `repo=/Users/mckerracher.joshua/Code/mcs-products-mono-ui.worktrees`, `change_id=WI-EVAL-003-A`, and `story_file=/Users/mckerracher.joshua/Code/Mine/agent-runner/eval/stories/EVAL-003.json`. That call raises `ValueError: Synthetic story fixture change_id does not match the runner change_id: fixture=EVAL-003, runner=WI-EVAL-003-A`. The persisted row for `job_fd5cff592ed7f6c1` shows `status=failed`, `exit_code=1`, `error_message=null`, `cassette_path=null`, and `events_path=agent-context/WI-EVAL-003-A/events.jsonl`, which is `0` bytes.

Relevant file paths:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/workflow_inputs.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/run.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/gui/index.html`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/server/runner_proc.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/server/jobs.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/eval/stories/EVAL-003.json`

## Local Opik trace bridge and Runs UI rendering

**Date**: 2026-04-29
**Source**: software_engineer_findings for `original_query`: Where is Opik tracing integrated, how does the UI render run data, what patterns/files/constraints matter for surfacing traces locally?

The repository now surfaces Opik trace boundaries in the local Runs UI by mirroring traced backend activity into the existing JSONL and SSE event pipeline instead of querying Opik from the frontend. The shared bridge lives in `ui_trace_bridge.py` and emits local `opik.start` / `opik.end` events that align with the existing run-event transport.

The bridge wraps the pre-existing Opik-traced workflow stage functions, evaluator loop traces, and loop-iteration spans. That means local run rendering now reflects the same trace boundaries already instrumented for Opik observability, while preserving the existing external Opik dashboard link for deeper inspection.

On the frontend, `gui/index.html` subscribes to the mirrored `opik.start` and `opik.end` SSE events and renders them with nesting depth plus compact metadata. The important local-surfacing pattern is: backend trace/span instrumentation stays authoritative, backend mirroring adapts those boundaries into the runner's JSONL/SSE event model, and the Runs UI only consumes that local event stream.

This avoids any frontend dependency on direct Opik queries, keeps local trace rendering aligned with the current run stream architecture, and makes `tests/test_ui_trace_bridge.py` the focused regression surface for bridge behavior.

Relevant file paths:

- `/Users/mckerracher.joshua/Code/Mine/agent-runner/ui_trace_bridge.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/opik_integration.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/steps.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/evaluator_optimizer_loops.py`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/gui/index.html`
- `/Users/mckerracher.joshua/Code/Mine/agent-runner/tests/test_ui_trace_bridge.py`

## Change 5035632 UOW-001 unsaved-order menu navigation implementation findings

**Date**: 2026-04-30
**Source**: software-engineer-hyperagent findings report for change 5035632 / UOW-001

UOW-001 implemented an orders-ui pending navigation contract for unsaved-order menu navigation. It did not add a new UI surface or a route guard. The durable pattern is to reuse the existing Order Changed confirmation handler while carrying a pending menu destination and navigation callback through middleware state.

The requested UOW spec path was initially missing and was materialized from `planning/tasks.yaml` task `T1` plus the `planning/assignments.json` `UOW-001` mapping at `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/5035632/execution/UOW-001/uow_spec.yaml`. The implementation target repository was `/Users/mckerracher.joshua/Code/mcs-products-mono-ui`; future work should not use the literal path containing `~/` for this change context.

Implementation details:

- `PendingChangeHandler` now includes `pendingNavigation` and `PendingNavigationData`.
- `SaveMiddleware.confirmAndNavigateToMenuDestination(destination, navigate)` checks `OrdersStore.hasChanged` through `SaveMiddleware.hasOrderChanged`.
- When the order is dirty, the selected destination is stored in `PendingHandlerMiddleware.pendingData`, and the existing Order Changed handler is opened.
- When the order is clean, navigation occurs immediately.
- `PendingHandlerMiddleware.pendingNavigation` reuses the existing Order Changed title, message, Continue label, and Cancel label.
- Accepting pending navigation calls the stored `navigate` callback.
- Rejecting pending navigation clears pending data and leaves the user on the current screen.

Files modified:

- `/Users/mckerracher.joshua/Code/mcs-products-mono-ui/libs/pearls/specimen-accessioning/ui/orders-ui/src/lib/types/pending-change-handler.ts`
- `/Users/mckerracher.joshua/Code/mcs-products-mono-ui/libs/pearls/specimen-accessioning/ui/orders-ui/src/lib/middleware/save.middleware.ts`
- `/Users/mckerracher.joshua/Code/mcs-products-mono-ui/libs/pearls/specimen-accessioning/ui/orders-ui/src/lib/middleware/pending-handler.middleware.ts`
- Jest specs corresponding to the modified middleware/types behavior.

Verification evidence:

- `npx nx test specimen-accessioning-orders-ui --skip-nx-cache` passed: 48 suites, 1499 passed, 11 skipped.
- `npx nx build rls-specimen-accessioning --skip-nx-cache` passed with pre-existing budget/CommonJS warnings.
- `npx eslint` on UOW-touched files passed.
- Broad `npx nx component-test specimen-accessioning-orders-ui --browser=chrome --skip-nx-cache` failed due pre-existing dirty `test-pill` component tests unrelated to UOW-001.
- Broad `npx nx lint specimen-accessioning-orders-ui --skip-nx-cache` failed due pre-existing dirty `test-pill` `any` warnings unrelated to UOW-001.
