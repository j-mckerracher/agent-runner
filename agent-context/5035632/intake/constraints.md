# Intake Constraints for 5035632

## Story Summary

- **Title:** Navigating away from page through menu
- **Source:** Azure DevOps work item [5035632](https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/5035632)
- **Work item type/state:** User Story / Active
- **Area path:** Mayo Collaborative Services\PEaRLS\Specimen Accessioning
- **Iteration path:** Mayo Collaborative Services\2026 Q2\Sprint 3

## Technical Context

- Target repo from runner context: `/Users/mckerracher.joshua/Code/Mine/agent-runner/~/Code/mcs-products-mono-ui`
- Project type normalized as `brownfield_ui` based on the supplied `mcs-products-mono-ui` target repository path.
- The supplied target repo path was checked as a literal path and was not found. The similarly named home-relative path `/Users/mckerracher.joshua/Code/mcs-products-mono-ui` exists, but intake preserves the runner-supplied value rather than rewriting it.
- No planning documents were explicitly referenced in the workflow context.

## Acceptance Criteria

- **AC1:** If the page is altered and unsaved, the unsaved changes dialog appears if I navigate away from the page using the menu
- **AC2:** If the page is altered and unsaved, clicking on the Triage Worklist pops up the unsaved changes dialog box
- **AC3:** If the page is altered and unsaved, clicking on the Specimen Accessioning pops up the unsaved changes dialog box

## Examples

- With altered, unsaved data, navigating away through the menu shows the unsaved changes dialog before continuing.
- With altered, unsaved data, clicking **Triage Worklist** shows the unsaved changes dialog box.
- With altered, unsaved data, clicking **Specimen Accessioning** shows the unsaved changes dialog box.

## Explicit Constraints

- Use the "Order Changed" modal provided in the story description/user context for the unsaved changes warning; do not invent a separate dialog pattern.
- The dialog trigger is limited to pages with altered, unsaved data.
- Menu-driven navigation away from the current page must be covered, including Triage Worklist and Specimen Accessioning menu entries.
- Acceptance criteria were normalized from lines prefixed "AC -" in System.Description because no dedicated Acceptance Criteria field was returned by the ADO CLI.

## Non-Functional Requirements

- None explicitly provided by the work item or user context.

## Referenced Planning Docs

- None explicitly provided.

## Source Attachments and Modal Reference

- Story description embeds image attachment `https://dev.azure.com/mclm/f25fdc8e-bb30-470e-b590-3b9d0576193f/_apis/wit/attachments/bc53319c-1268-449d-b5c3-f229fbf27593?fileName=image.png` with alt text `Image`.
- User context explicitly requires using the **"Order Changed"** modal provided in the story description.

## Open Questions

- The supplied target repo path does not exist as a literal path because it contains a "~/" segment after the agent-runner directory; intake preserves the runner-supplied value, but downstream stages may need path resolution.
- The story description embeds an ADO image attachment for the modal; intake preserved the attachment URL in raw_input but did not transcribe visual details beyond the explicit "Order Changed" modal requirement.

## Librarian Queries

- Scoped lesson query returned full confidence with no applicable intake lessons.
