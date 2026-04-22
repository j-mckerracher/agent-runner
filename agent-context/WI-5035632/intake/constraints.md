# Constraints and Technical Context

## Story Summary
**Work Item**: WI-5035632 | **Type**: User Story | **Status**: Active  
**Project**: Mayo Collaborative Services | **Area**: PEaRLS > Specimen Accessioning  
**Sprint**: 2026 Q2 > Sprint 2 | **Story Points**: 3 | **Priority**: 4

---

## Technical Context

### Application Scope
- **Codebase**: mcs-products-mono-ui (React/TypeScript monorepo)
- **Feature Area**: Specimen Accessioning module
- **Related Parent Epic**: WI-5002349
- **Related Tests**: 10 child test work items (WI-5039212 through WI-5039220)

### Current Implementation Status
- Work item is in **Active** status as of 2026-04-20
- Board column: **Active**
- Revision: 16 (multiple iterations/updates)
- Linked to PR #466118 (related pull request exists)

### Business Requirements
- **Value Area**: Business
- **Business Priority**: 4 (standard priority)
- **User Story Focus**: Dialog visibility for unsaved changes during menu navigation
- **Impact**: Prevents accidental data loss when navigating away from forms

---

## Acceptance Criteria Details

### AC1: Dialog Display on Menu Navigation
When the page state contains unsaved changes and the user initiates navigation via menu:
- The unsaved changes dialog MUST be displayed
- Dialog should appear within reasonable UX timeframe
- Applies to all menu-based navigation patterns

### AC2: Dialog Options
The dialog MUST provide three clear options:
- **Save**: Persist changes and proceed with navigation
- **Discard**: Abandon changes and proceed with navigation
- **Cancel**: Abort navigation and return to form

### AC3: Universal Menu Coverage
The unsaved changes detection and dialog MUST work for:
- All menu navigation methods in the application
- Different form types and edit scenarios
- Consistent behavior across the Specimen Accessioning module

---

## Examples & Test Scenarios

### Example 1: Form Edit + Menu Navigation
1. User opens specimen form and makes edits
2. User clicks a menu item to navigate away
3. **Expected**: Unsaved changes dialog appears
4. **User Action**: Can choose to save, discard, or cancel

### Example 2: Multiple Field Edits
1. User edits multiple fields across a complex form
2. Each field has pending changes (not yet saved)
3. User clicks menu navigation
4. **Expected**: Single consolidated dialog for all pending changes
5. **Note**: May need aggregation of multiple field-level changes

### Example 3: No Changes Scenario
1. User views a specimen record (read-only or no edits)
2. User clicks menu to navigate
3. **Expected**: Dialog does NOT appear
4. **Behavior**: Navigation proceeds immediately

---

## Known Related Work

### Child Test Work Items
The following test cases are associated with this story (may inform AC validation):
- WI-5039212, WI-5039213, WI-5039214, WI-5039215, WI-5039216, WI-5039217, WI-5039218, WI-5039219, WI-5039220

### Linked Pull Request
- PR #466118 (linked in ADO) - may contain draft implementation or related changes

---

## Constraints & Assumptions

### Technical Constraints
- Must integrate with existing unsaved changes detection system
- Must not break existing form validation or save logic
- Dialog styling must match current application theme
- No changes to data model or API contracts required

### Scope Constraints
- Focused on **menu-based** navigation (not route-based or direct URL changes)
- Limited to Specimen Accessioning application context
- Assumes existing form state management framework is in use

### Assumptions
- Form state is already tracked (dirty/clean flags exist)
- Dialog component infrastructure is already in place
- Menu navigation routing is centralized and can intercept navigation events
- User has appropriate permissions to modify specimen records

---

## Open Questions & Gaps

### Clarifications Needed
1. **Dialog Routing**: Should the dialog appear for ALL menu destinations or specific routes?
2. **Form Types**: Does this apply to all form types (add, edit, view-with-edit, etc.)?
3. **Stale Data**: If multiple users edit the same record, should dialog consider that?
4. **Auto-Save**: Should there be any auto-save functionality that affects dialog behavior?

### Implementation Unknowns
- Current form state management pattern in codebase (Redux, Context, other)
- Existing dialog component capabilities
- Menu routing architecture and interception points
- Current unsaved changes detection mechanism

### Risk Factors
- Dialog might appear too frequently (annoying users) or too rarely (failing AC)
- Menu navigation implementation may vary across different routes
- Timing of dialog appearance vs. route change may create race conditions

---

## Repository Context

### Target Codebase
```
/Users/mckerracher.joshua/Code/mcs-products-mono-ui
```

### Suspected Implementation Areas
- Form components (likely in `src/features/specimen-accessioning/`)
- Menu/navigation components (likely in `src/components/layout/` or `src/navigation/`)
- Form state management (likely global store or component-level context)
- Dialog/modal components (likely in `src/components/dialogs/` or similar)

---

## Effort & Complexity Assessment

- **Story Points**: 3 (medium effort)
- **Complexity**: Medium
  - Requires understanding existing form state management
  - May need to hook into menu navigation event system
  - Dialog component integration is likely straightforward
- **Risk Level**: Medium
  - Could affect user experience if dialog behavior is incorrect
  - Dependent on correct form state detection

---

## ADO Integration Details

- **Organization**: mclm (Mayo Collaborative Learning)
- **Project**: Mayo Collaborative Services
- **Work Item Type**: User Story
- **Created**: 2026-04-15 by Kosiorek, Derek J.
- **Assigned To**: McKerracher, Josh S.
- **Current State**: Active
- **Revision**: 16
- **ADO URL**: https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/5035632

---

## Notes for Planning & Implementation

1. **Planning Stage**: May need to explore menu routing architecture first
2. **Implementation Stage**: Start with form state detection, then integrate dialog
3. **Testing Stage**: Validate all menu navigation paths trigger dialog appropriately
4. **Review Stage**: Confirm user feedback on dialog frequency/timing
