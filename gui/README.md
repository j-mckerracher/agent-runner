# GUI structure

The GUI used to be a single `index.html` containing markup, styles, application logic, and the animated background. It is now split by concern so the entry point is easier to scan and each large area has a clear home.

## Files

- `index.html` keeps the static DOM structure for all views. It should stay mostly declarative.
- `assets/css/app.css` contains all visual styling, grouped by shell/view so maintainers can search by screen name.
- `assets/js/app-core.js` contains shared state, API helpers, repository pickers, formatting helpers, metrics helpers, and workflow rail rendering.
- `assets/js/telemetry.js` owns the Run Telemetry view, including filters, tables, charts, detail panels, and polling.
- `assets/js/app-shell.js` owns navigation, health checks, and Settings. Settings loads early because it fills runner/model options used elsewhere.
- `assets/js/runs.js` owns the Runs view, including history filters, run submission, terminal rendering, trace summaries, and SSE lifecycle.
- `assets/js/content-views.js` owns Agents, Evaluation Results, and Run Evaluations.
- `assets/js/background.js` owns the Three.js background canvas and is intentionally isolated from application logic.

## Maintenance notes

The JavaScript files are loaded as plain scripts, not modules. That preserves the previous browser-global behavior while still making the codebase navigable. When adding new behavior, prefer putting it in the file for the owning view. Use comments to explain cross-view coupling, API shape assumptions, or non-obvious UI state transitions; avoid comments that simply repeat what the next line does.
