Intake constraints and open questions for EVAL-002

Supported technical context (explicit in fixture):
- Exact files and symbol names referenced in acceptance criteria; multiple files under libs/pearls/sendouts/* and Cypress component tests.
- Runtime commands required by acceptance criteria: `npx nx component-test rls-sendouts-ui-manifest-ui --browser=chrome --skip-nx-cache` and `npx nx build rls-sendouts-ui-manifest-ui --skip-nx-cache`.

Examples & non-functional requirements (preserved from fixture):
- Examples: empty-state behavior, specimen count behavior, locator/harness benefits for test selectors.
- NFRs: machine-checkable ACs; story calibrated to be difficult (approx. 50/100 target average score).

Referenced planning docs:
- None provided in the fixture. No external planning_docs were ingested.

Open questions / gaps (recorded, not inferred):
1. Runner-provided target repo path contains an embedded tilde segment: 
   "/Users/mckerracher.joshua/Code/Mine/agent-runner/~/Code/mcs-products-mono-ui". Is the '~' literal or intended as the user's home expansion? Intake preserves the provided path as-is; downstream runner must resolve if needed.

2. The fixture forbids modifying evaluation logic for EVAL-001 beyond a refactor into a story-dispatch path. The exact boundary of acceptable refactorings is unspecified; downstream stages should validate any refactor does not change evaluation outcomes.

Preserved source and provenance:
- Source: synthetic local fixture
- Fixture path: /Users/mckerracher.joshua/Code/Mine/agent-runner/eval/stories/EVAL-002.json
- No ADO metadata present in the fixture.

Open questions count: 2

Notes for downstream stages:
- Acceptance criteria were normalized into AC1..AC4 in story.yaml and must be treated as machine-verifiable checks. Do not silently expand or relax exact symbol names, data-test-id values, or test titles.
