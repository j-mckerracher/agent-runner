# Constraints and Open Questions

Source: synthetic fixture (local)

## Technical context (from fixture)

- Target files and symbols are explicitly listed in the acceptance criteria (see story.yaml).
- File paths follow the monorepo Nx Angular layout (libs/pearls/specimen-accessioning/...).
- Required symbol names, data-test-id values, CSS class names, and Cypress test titles must match exactly for machine validation.

## Examples & non-functional requirements

- Examples and NFRs preserved from the fixture: badge display behavior for normal, overflow, and empty states; machine-checkable criteria; do not add new npm deps.

## Referenced planning docs

- None provided in the fixture.

## Open questions / gaps (explicitly recorded)

1. TestModel shape: the criteria reference TestModel fields (specimens, preferredNumOfSpecimens). Confirm exact type/location of TestModel in the codebase to ensure imports and typing match.
2. CSS scoping and styling conventions: confirm whether to add classes in component stylesheet or global styles; follow existing test pill patterns when in doubt.
3. Verify that the code_repo path provided by the runner maps to the intended workspace location for edits: 
   - Runner-provided code_repo: /Users/mckerracher.joshua/Code/Mine/agent-runner/~/Code/mcs-products-mono-ui

## Execution blockers

- No blockers in the fixture itself. Any blockers should be recorded by downstream stages if repository lookups reveal missing symbols or unexpected file shapes.

## Notes for downstream stages

- Raw fixture preserved in intake/story.yaml under raw_input.
- Acceptance criteria were normalized to AC1..AC5. Keep exact strings and file locations unchanged when implementing to keep evaluation machine-verifiable.
