# Constraints and Technical Context — TEST-MEDIUM-001

## Story Type and Scope

**Synthetic Local Fixture**: This story comes from `/Users/mckerracher.joshua/Code/Mine/agent-runner/agent-context/test-fixtures/synthetic_story_medium.json` and is used for agent-runner workflow validation testing. It is not a live Azure DevOps work item.

**Feature Domain**: Mayo Collaborative Services / Reference Laboratory Services (RLS) — Send-Outs Module
- **Codebase**: `mcs-products-mono-ui` (Angular + Nx)
- **Target Library**: `libs/pearls/sendouts/` (shared RLS send-out domain)
- **Difficulty**: Medium — exercises multi-task decomposition, codebase discovery, and Angular signal patterns

## Explicit Constraints

All constraints are **mandatory** and form part of the definition of done:

1. **Client-Side Validation Only**
   - Do not modify HTTP service calls, API contracts, or server-side behavior
   - Validation is a UI-side concern only

2. **UI Pattern Compliance**
   - Validation error display must follow the existing PrimeNG form error pattern used elsewhere in the sendouts or shared UI library
   - Do not introduce new error display conventions

3. **Button State Wiring**
   - Wire the disabled state to the existing Add Specimen button
   - Do not add a new button as part of this story

4. **State Management Boundary**
   - Validation state must NOT persist into the NgRx Signals store
   - Do not affect saved manifest data
   - Local component state only

5. **Scope Boundary**
   - Scope is limited to the barcode entry sub-form within the send-out manifest specimens section
   - Do not extend validation to other form inputs in parallel

## Non-Functional Requirements

1. **Real-Time Synchronous Validation**
   - Validation must run on each keystroke with no async validators
   - Avoid UI flicker or delayed feedback

2. **Test Harness Exposure**
   - The inline error message element must carry a `data-test-id` attribute
   - Expose the element through the Cypress test harness for assertion in component tests
   - Example: error container should be selectable as `.barcode-error-message` or similar

3. **Accessibility and Selectors**
   - Barcode input and Add button must each have `data-test-id` attributes if they do not already
   - Add these attributes as part of this story

## Format Specification

**Barcode Format**: 8–16 alphanumeric characters, no special characters
- Valid examples: `ABC12345`, `ABCD1234`, `ABC123456789ABC`
- Invalid examples: `AB12-CD56` (hyphen), `AB1` (too short), `ABC12@45` (special char), `AB CD 12` (space)

**Error Message**: Exact text to display is `Invalid barcode format`

**Field Behavior**:
- Empty field: Add button disabled, no error message shown (field not yet touched)
- Invalid format: Add button disabled, error message shown beneath input
- Valid format: Add button enabled, no error message

## Examples and Acceptance Paths

### Happy Path (Valid Barcode)
1. Technician types `ABC12345` (8 valid alphanumeric chars)
2. No error message appears
3. Add button remains enabled
4. Technician clicks Add
5. Barcode is submitted, field clears, validation resets
6. Form returns to ready state for next entry

### Error Path (Invalid Format)
1. Technician types `AB12-CD56` (contains hyphen, invalid)
2. Error message "Invalid barcode format" appears beneath input
3. Add button becomes disabled
4. Technician corrects input to `AB12CD56`
5. Error message disappears, Add button becomes enabled
6. Technician clicks Add or continues

### Untouched Field
1. Form loads, barcode input is empty
2. Add button is disabled (cannot submit empty)
3. No error message is displayed (field not yet touched)

## Related Codebase Elements

- **Send-Outs Feature Library**: `libs/pearls/sendouts/`
- **PrimeNG UI Library**: Used throughout the MCS monorepo for form controls
- **Angular Signals**: Modern state management pattern in the codebase
- **Cypress Test Harnesses**: Located adjacent to components, test via page objects

## Open Questions and Ambiguities

- **Existing error pattern**: Requires discovery of the exact PrimeNG error pattern used in the sendouts or shared library (how errors are styled, positioned, and accessible)
- **Button element ref**: Requires locating the existing Add Specimen button in the component to wire the disabled state
- **Data-test-id conventions**: Verify naming convention for test IDs in the sendouts library (e.g., `barcode-error`, `.barcode-input`, etc.)

## Non-Goals

- Do not add server-side validation
- Do not modify API contracts
- Do not persist validation state to the store
- Do not add new buttons or form fields
- Do not extend validation to other form sections

## Confidence Level

**High Confidence**: Acceptance criteria are explicit, examples cover the key scenarios, and scope boundaries are clear. No ambiguous requirements. Synthetic fixture is well-formed and does not rely on external planning documents or ADO metadata.

**Artifacts Ingested**:
- Synthetic fixture: `synthetic_story_medium.json`
- No external planning documents referenced

**Context Confidence Score**: 95%
