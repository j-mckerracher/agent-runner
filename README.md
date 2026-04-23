# agent-runner

A workflow runner that executes multi-stage AI agent pipelines against either:
- **Local synthetic story fixtures** — for offline testing without Azure DevOps
- **Live Azure DevOps work items** — for integration with ADO projects

The workflow executes 6 stages: intake → planning (task-gen) → assignment → implementation → QA → lessons-optimization.

---

## Quick Start

### Run with the bundled TEST-AC-001 synthetic story (default)

```bash
python run.py --repo /absolute/path/to/target/repo
```

This uses `agent-context/test-fixtures/synthetic_story.json` by default—no additional arguments needed.

### Run with a custom synthetic story fixture

```bash
python run.py --repo /absolute/path/to/target/repo --story-file /path/to/custom_story.json
```

The story file can be relative or absolute. Use `~` for the home directory:

```bash
python run.py --repo /absolute/path/to/target/repo --story-file ~/my_fixtures/my_story.json
```

### Run against Azure DevOps

```bash
python run.py --repo /absolute/path/to/target/repo --ado-url 'https://dev.azure.com/<org>/<project>/_workitems/edit/123456'
```

---

## Synthetic Mode vs. ADO Mode

The workflow operates in two distinct modes, determined automatically by the arguments you provide.

### Synthetic Mode (Local Testing)

**Use when:**
- You want to test the workflow locally without Azure DevOps
- You're developing or debugging workflow stages
- You want repeatable, deterministic test scenarios
- You don't have Azure DevOps access or credentials

**Characteristics:**
- ✅ No external dependencies (no Azure CLI, no ADO credentials needed)
- ✅ Fast, repeatable execution
- ✅ Deterministic output (same input = same output)
- ✅ Offline execution (no network calls)
- ✅ Story input is a local JSON file
- ✅ Intake artifacts tagged with `project_type: 'synthetic-fixture'`

**Workflow:**
1. Run `python run.py --story-file <path>` or rely on default bundled fixture
2. Intake stage reads local JSON, normalizes to canonical artifacts
3. Downstream stages detect synthetic mode via config.yaml markers
4. All ADO-specific operations are skipped

### ADO Mode (Live Work Items)

**Use when:**
- You're integrating with a live Azure DevOps project
- You want to track execution against a real work item
- Your story requires live domain context from ADO

**Characteristics:**
- ✅ Reads from live Azure DevOps
- ✅ Creates ADO metadata in artifacts
- ✅ Logs back to work item discussions (when applicable)
- ❌ Requires Azure CLI configured and valid credentials
- ❌ Network-dependent (may be slower)

**Workflow:**
1. Run `python run.py --ado-url <url>` with a valid work item URL
2. Intake stage fetches from ADO and normalizes
3. Downstream stages see ADO metadata and may make back-calls to ADO

---

## Synthetic Fixture Format

All synthetic story fixtures must be valid JSON with a specific structure.

### Required Fields

Every synthetic story **must** include:

- **`change_id`** (string) — Unique identifier for this story (e.g., `TEST-AC-001`, `WI-12345`). Can also be provided via `--change-id` argument.
- **`title`** (string, non-empty) — One-line title for the story
- **`description`** (string, non-empty) — Multi-line narrative explaining the story's purpose
- **`acceptance_criteria`** (list or object, non-empty) — Success criteria; see details below

### Acceptance Criteria Format

You may express acceptance criteria as either:

**Option 1: List of strings** (simple for small stories)
```json
{
  "acceptance_criteria": [
    "Users can log in with valid credentials",
    "Invalid credentials show an error message",
    "Session expires after 30 minutes of inactivity"
  ]
}
```

**Option 2: Keyed object** (explicit labels for complex stories)
```json
{
  "acceptance_criteria": {
    "AC1": "Users can log in with valid credentials",
    "AC2": "Invalid credentials show an error message",
    "AC3": "Session expires after 30 minutes of inactivity"
  }
}
```

**Validation rules:**
- Must not be empty (at least one item)
- All items must be non-empty strings (no `null`, no whitespace-only strings)
- If using list format, will be automatically keyed as `AC1`, `AC2`, `AC3`, etc. during intake

### Optional Fields

Enhance your fixture with optional fields:

- **`examples`** (list of strings) — Usage examples or clarifications
- **`constraints`** (list of strings) — Limitations or boundary conditions
- **`non_functional_requirements`** (list of strings) — Performance, security, compatibility requirements
- **`raw_input_notes`** (object) — Metadata about the fixture itself (purpose, owner, etc.)
- **`ado_metadata`** (object) — If your story originated from ADO; contains work item context (optional, not used in synthetic mode)

### Example Fixture

```json
{
  "change_id": "TEST-AC-001",
  "title": "Synthetic workflow smoke test story",
  "description": "Use this local story fixture to validate the agent workflow without connecting to Azure DevOps. The scenario is intentionally small and deterministic.",
  "acceptance_criteria": [
    "The workflow can start from a local synthetic story fixture",
    "The intake stage preserves the raw synthetic story input",
    "ADO-specific actions are skipped for synthetic fixtures"
  ],
  "examples": [
    "Run with: python run.py --story-file /path/to/story.json",
    "Use the generated intake artifacts to verify all workflow stages"
  ],
  "constraints": [
    "This story exists only for workflow testing",
    "Keep any code changes minimal and easy to verify"
  ],
  "non_functional_requirements": [
    "The workflow should fail fast on malformed fixtures",
    "The workflow should remain compatible with existing artifacts"
  ],
  "raw_input_notes": {
    "purpose": "End-to-end workflow validation",
    "owner": "agent-runner"
  }
}
```

---

## Creating Custom Synthetic Fixtures

To test the workflow with your own story:

### Step 1: Create a JSON file

Create a file (e.g., `my_story.json`) with the structure above. Start with a copy of the bundled example:

```bash
cp agent-context/test-fixtures/synthetic_story.json my_story.json
```

### Step 2: Edit the required fields

Update `change_id`, `title`, `description`, and `acceptance_criteria` to match your scenario:

```json
{
  "change_id": "MY-CUSTOM-001",
  "title": "My custom workflow test",
  "description": "Testing a specific workflow scenario...",
  "acceptance_criteria": [
    "First success criterion",
    "Second success criterion"
  ]
}
```

### Step 3: Run the workflow

```bash
python run.py --repo /path/to/target/repo --story-file /path/to/my_story.json
```

### Step 4: Review the intake artifacts

The workflow creates artifacts in `agent-context/<change-id>/intake/`:

```
agent-context/MY-CUSTOM-001/intake/
├── story.yaml          # Normalized story with keyed acceptance criteria
├── config.yaml         # Workflow configuration (includes project_type marker)
└── constraints.md      # Extracted constraints and context
```

Downstream artifacts appear in:
```
agent-context/MY-CUSTOM-001/planning/     # tasks.yaml, assignments.json
agent-context/MY-CUSTOM-001/execution/    # impl_report.yaml per UoW
agent-context/MY-CUSTOM-001/qa/           # qa_report.yaml
```

---

## Bundled Test Fixtures

Two built-in fixtures are provided:

| File | Change ID | Complexity | Purpose |
|------|-----------|-----------|---------|
| `agent-context/test-fixtures/synthetic_story.json` | `TEST-AC-001` | Simple | Meta smoke-test — validates workflow stages themselves |
| `agent-context/test-fixtures/synthetic_story_medium.json` | `TEST-MEDIUM-001` | Medium | RLS Send-Outs domain — tests codebase discovery and multi-task decomposition |

---

## Troubleshooting & Error Handling

### Error: "Synthetic story fixture not found"

**Cause:** The fixture file does not exist at the provided path.

**Fix:**
- Verify the file path is correct
- Use absolute paths or expand `~` manually
- Check file permissions (must be readable)

Example:
```bash
# ❌ Wrong
python run.py --story-file ./my_story.json

# ✅ Correct
python run.py --story-file /Users/you/projects/my_story.json
python run.py --story-file ~/projects/my_story.json
```

### Error: "Synthetic story fixture must be a JSON object"

**Cause:** The file is not valid JSON, or contains a JSON array instead of an object.

**Fix:**
- Validate your JSON syntax (use a JSON linter)
- Ensure the top level is a `{ }` object, not a `[ ]` array
- Check for trailing commas or missing quotes

Example:
```json
// ❌ Wrong — top level is an array
[
  { "change_id": "TEST-001", ... }
]

// ✅ Correct — top level is an object
{
  "change_id": "TEST-001",
  "title": "...",
  ...
}
```

### Error: "Synthetic story fixture is missing required field(s)"

**Cause:** One or more required fields (`change_id`, `title`, `description`, `acceptance_criteria`) are missing or empty.

**Fix:**
- Add the missing field to your JSON
- Ensure all required fields are non-empty strings (or non-empty arrays/objects for `acceptance_criteria`)

Example:
```json
{
  "change_id": "MY-STORY-001",  // ✅ Required
  "title": "My Story",           // ✅ Required
  "description": "Details...",   // ✅ Required
  "acceptance_criteria": ["AC1"] // ✅ Required
}
```

### Error: "Synthetic story fixture acceptance_criteria must be a non-empty list of strings or map of strings"

**Cause:** The `acceptance_criteria` field is malformed (empty, contains non-strings, etc.).

**Fix:**
- Ensure it's either a non-empty list or a non-empty object
- All items must be non-empty strings
- No `null`, empty strings, or numeric/boolean values

Example:
```json
// ❌ Wrong — empty list
{ "acceptance_criteria": [] }

// ❌ Wrong — contains null
{ "acceptance_criteria": ["AC1", null, "AC3"] }

// ❌ Wrong — map with non-string value
{ "acceptance_criteria": { "AC1": "text", "AC2": 123 } }

// ✅ Correct — list of strings
{ "acceptance_criteria": ["AC1", "AC2", "AC3"] }

// ✅ Correct — map of strings
{ "acceptance_criteria": { "AC1": "text", "AC2": "more text" } }
```

### Error: "Synthetic story fixture change_id does not match the runner change_id"

**Cause:** You provided both a fixture with `change_id` and `--change-id` argument, and they differ.

**Fix:**
- Either remove the `--change-id` argument (use the fixture's value)
- Or remove `change_id` from the fixture and provide it via `--change-id`
- Or ensure both values match

Example:
```bash
# ❌ Conflict
python run.py --story-file my_story.json --change-id OTHER-ID

# ✅ Use fixture's change_id
python run.py --story-file my_story.json

# ✅ Override fixture's change_id (fixture must not have change_id field)
python run.py --story-file my_story.json --change-id MY-ID
```

### Error: "Provide either ado_url or story_file, not both"

**Cause:** You provided both `--ado-url` and `--story-file` arguments.

**Fix:**
- Choose one mode: synthetic (`--story-file`) or ADO (`--ado-url`)
- Remove the unused argument

Example:
```bash
# ❌ Wrong
python run.py --ado-url https://... --story-file my_story.json

# ✅ Correct — synthetic mode
python run.py --story-file my_story.json

# ✅ Correct — ADO mode
python run.py --ado-url https://dev.azure.com/...
```

---

## Understanding Synthetic Mode Markers

When the intake stage processes a synthetic fixture, it creates artifacts with markers that signal downstream stages to skip ADO operations.

### In `config.yaml`

Look for the synthetic mode marker:
```yaml
project_type: 'synthetic-fixture'
```

This tells downstream stages: "This is a local test, no ADO integration needed."

### In `story.yaml`

Look for the preservation of raw input:
```yaml
raw_input:
  source_type: synthetic_fixture
  fixture_path: agent-context/test-fixtures/synthetic_story.json
  original_fixture: |
    { ... original JSON ... }
```

This confirms:
1. ✅ The story came from a synthetic fixture (not ADO)
2. ✅ The original fixture JSON is preserved exactly
3. ✅ The path to the fixture is documented

### Absence of `ado_provenance`

Check that `story.yaml` does **not** contain:
```yaml
ado_provenance:  # This field should NOT exist for synthetic fixtures
  work_item_id: ...
  ...
```

The absence of this field signals: "No Azure DevOps metadata present; this is a local-only workflow."

---

## Running Tests & Validation

### Run all unit and integration tests

```bash
python -m pytest tests/ -v
```

### Run only synthetic workflow tests

```bash
python -m pytest tests/test_steps_and_run.py::FullSyntheticWorkflowIntegrationTests -v
```

### Validate fixture format

```bash
python -c "
from workflow_inputs import load_story_fixture
try:
    fixture = load_story_fixture('path/to/your_story.json')
    print('✅ Fixture is valid')
except ValueError as e:
    print(f'❌ Fixture error: {e}')
"
```

### View command-line help

```bash
python run.py --help
```

Output:
```
usage: run.py [-h] [--repo REPO] [--change-id CHANGE_ID] [--ado-url ADO_URL]
              [--story-file STORY_FILE] [--runner {claude,copilot,gemini}]
              [--gemini-model {gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash}]

Run the agent workflow against either a live ADO story or a local synthetic story fixture.

optional arguments:
  -h, --help            show this help message and exit
  --repo REPO           Target repository path. Defaults to the current working directory.
  --change-id CHANGE_ID
                        Workflow change id. Optional for ADO URLs that end in a work item id or for fixtures that include change_id.
  --ado-url ADO_URL     Azure DevOps work item URL for a live intake run.
  --story-file STORY_FILE
                        Path to a synthetic story fixture JSON file for local testing. Defaults to agent-context/test-fixtures/synthetic_story.json
                        when neither --ado-url nor --story-file is provided.
  --runner {claude,copilot,gemini}
                        Agent runner to use: 'claude' (Claude Code CLI), 'copilot' (GitHub Copilot CLI), or 'gemini' (Gemini CLI). Defaults to 'claude'.
  --gemini-model {gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash}
                        Gemini model to use when --runner gemini. Defaults to 'gemini-2.5-flash'.
```

---

## Workflow Stages

The synthetic workflow executes these stages in order:

1. **Intake** — Reads the fixture, normalizes to canonical artifacts (story.yaml, config.yaml, constraints.md)
2. **Task Generation** — Reads story context, generates task decomposition (tasks.yaml)
3. **Task Assignment** — Reads tasks, creates execution schedule (assignments.json)
4. **Implementation** — Executes each Unit of Work (UoW), creates impl_report.yaml per UoW
5. **QA** — Validates all artifacts and generates qa_report.yaml
6. **Lessons Optimization** — Captures learnings and best practices

All artifacts are stored under `agent-context/<change-id>/`.

---

## Local Validation

Verify your setup and fixtures:

```bash
# Run all tests
python -m unittest discover -s tests -v

# Or with pytest
python -m pytest tests/ -v

# View available fixtures
ls -la agent-context/test-fixtures/

# Run with the bundled fixture
python run.py --repo /path/to/target/repo
```

---

## Contact & Support

For issues with synthetic fixtures or the workflow runner, check:
- This README (especially **Troubleshooting & Error Handling**)
- `python run.py --help` for argument details
- Test files in `tests/` for example usage patterns
- Intake artifacts in `agent-context/<change-id>/intake/` for error diagnostics
