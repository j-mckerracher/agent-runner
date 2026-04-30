# Constraints and Open Questions

## Technical Context
- Target APIs: `rls-orders-cnsmr-api` and `rls-docgen-system-api`.
- Runtime Environment: .NET 8 on alpine (Cloud Run).
- Requirement: Graceful shutdown via SIGTERM, host lifetime handling, Serilog flushing, and no abrupt termination.
- Documentation requirement: Document remaining APIs (`rls-orders-orch-api`, `rls-orders-data-api`) that require this pattern in the future.

## Open Questions
- Are there specific existing test suites to ensure no regressions are introduced (AC5)?
- Is there a standardized project-wide implementation for .NET graceful shutdown already in use that should be followed?
