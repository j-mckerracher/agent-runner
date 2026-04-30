# Constraints for PR-468504 intake

## Technical context

- **Source work item:** Azure DevOps support request `5043919` in `Mayo Collaborative Services`
- **Target repo for this workflow:** `/Users/mckerracher.joshua/Code/rls-orders-cnsmr-api`
- **Project type:** Brownfield change against an existing API
- **Story scope captured from ADO:** graceful shutdown behavior for `rls-orders-cnsmr-api` and `rls-docgen-system-api`, with the runner targeting only `rls-orders-cnsmr-api` for this intake
- **Explicit out-of-scope follow-up:** `rls-orders-orch-api` and `rls-orders-data-api`

## Examples and implementation notes

- The user explicitly stated that the cancellation token should be propagated to the same level as the completed work in `rls-docgen-system-api` PR `468504`.
- The referenced PR demonstrates the desired depth of propagation through request handling layers:
  - pass `CancellationToken` from controller entrypoint into business logic
  - propagate cancellation through validator and composer calls
  - thread cancellation through deeper helper/component layers
  - add cooperative cancellation checks at key work boundaries
- The linked PR description is preserved in `intake/story.yaml` under `raw_input.linked_artifacts.pull_request`.

## Non-functional requirements

- Behave appropriately for the Cloud Run container lifecycle on .NET 8/alpine.
- On `SIGTERM`, stop taking new work and allow in-flight work to complete or cancel cleanly before exit.
- Flush Serilog sinks during shutdown.
- Avoid unhandled exceptions and abrupt termination during shutdown.
- Do not introduce regressions.

## Planning docs

- No planning documents were explicitly referenced in the ADO work item, linked PR metadata, or user-supplied context.

## Open questions

1. What shutdown timeout or termination budget should the implementation assume for the target Cloud Run deployment?
2. Which exact `rls-orders-cnsmr-api` layers should mirror the cancellation-token propagation depth shown in `rls-docgen-system-api` PR `468504`?
