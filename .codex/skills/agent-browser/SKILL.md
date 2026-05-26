---
name: agent-browser
description: Automates browser interactions including web testing, web scraping, form filling, screenshots, and data extraction using a highly optimized, context-efficient text snapshot and element reference system. Use this skill whenever the user asks to look up a webpage, test an application, extract data from a site, or perform interactions inside a web browser.
---

# Skill Instructions

The `agent-browser` tool is a high-performance browser automation CLI designed specifically for AI agents. It utilizes a text-based snapshot system that translates web page layouts into a compact tree representation. Elements are assigned temporary reference IDs (e.g., `@e1`, `@e2`), enabling you to interact with elements accurately without sending large blocks of DOM text or raw HTML into the context window.

## Core Lifecycle Workflow
Every browser session follows a strict state-based progression. Always adhere to these operational phases:
1. **Initialize/Navigate:** Open a website using `agent-browser open <url>`.
2. **Inspect Structure:** Call `agent-browser snapshot -i` to view a compact, text-filtered tree of interactive elements with context-assigned identifiers (e.g., `@e1`).
3. **Interact:** Issue specific mutation commands targeting these references (e.g., `click`, `fill`, `select`).
4. **Observe:** Verify state changes using subsequent `snapshot`, `get`, `is`, or `screenshot` commands.
5. **Terminate:** Explicitly release system resources and shut down the active browser state with `agent-browser close`.

---

## 1. Environment Initialization & Setup
Before executing automation routines, ensure the local binary and browser engine binaries are correctly provisioned.

* **Install Browser Dependencies:** Provision the headless browser instance locally. Must be run before initial execution.
  ```bash
  agent-browser install

```

---

## 2. Navigation, Tabs, & Frame Management

These commands govern window contexts, tab sets, and navigation rules.

* **Open / Navigate:** Starts the daemon if it is not already running and routes to the designated URL.
```bash
agent-browser open [https://example.com](https://example.com)

```


* **List Active Tabs:** Lists all open tabs along with their IDs and user-defined labels.
```bash
agent-browser tab

```


* **Create New Tab:** Opens a clean browser tab context. Specifying a label allows programmatic targeting.
```bash
agent-browser tab new "[https://docs.api.com](https://docs.api.com)"
agent-browser tab new --label docs "[https://docs.api.com](https://docs.api.com)"

```


* **Switch Active Tab:** Changes active tracking context to an alternate tab using its sequence ID (`t1`, `t2`) or text label.
```bash
agent-browser tab t2
agent-browser tab docs

```


* **Close Active Tab:** Closes the target tab explicitly.
```bash
agent-browser tab close docs

```


* **Frame Navigation:** Directs focus inside an isolated `iframe` or resets context back to the primary page DOM tree.
```bash
agent-browser frame "#payment-processor-frame"
agent-browser frame @e12
agent-browser frame main

```


* **Close Browser Instance:** Standard termination command. Safely releases memory pools and kills active processes.
```bash
agent-browser close

```



---

## 3. Page Inspection & Structural Analysis

Rather than parsing verbose DOM text, use these optimization features to analyze page layouts.

* **Get Interactive Snapshot (Recommended):** Returns an explicit text hierarchy of elements that can be clicked, typed into, or read. This is your primary method for generating element references.
```bash
agent-browser snapshot -i

```


*Example Output Structure:*
```text
- heading "Example Domain" [ref=e1]
- input "Search queries" [ref=e2]
- link "More information..." [ref=e3]

```


* **Get Unfiltered Snapshot:** Captures the complete structural node tree including static layouts.
```bash
agent-browser snapshot

```


* **Capture Page Visuals:** Exports a visual screenshot to file. Essential for visual checking or debugging page layouts.
```bash
agent-browser screenshot page_state.png

```



---

## 4. Element Interactions (Mutations)

Use the references generated from your latest `snapshot` command (prefixed with `@`) to interact with the page. You can also use traditional CSS selectors or text queries.

* **Click Element:** Simulates an explicit click on a targeting anchor, button, or structural node.
```bash
agent-browser click @e3
agent-browser click "#submit-button"

```


* **Fill / Enter Text:** Places target text directly into fields. Automatically clears existing value buffers.
```bash
agent-browser fill @e2 "Search Query Text"
agent-browser fill "#email-input" "user@example.com"

```


* **Find Element & Click (Combined):** Searches the DOM tree for specific roles and triggers a mutation sequentially.
```bash
agent-browser find role button click --name "Submit"

```


* **Scroll Document Viewport:** Positions viewport locations.
```bash
agent-browser scroll down
agent-browser scroll up

```



---

## 5. State Assertion & Data Extraction

These tools extract state data, element values, and attributes directly from the active viewport.

* **Get Text Content:** Pulls the visible inner text from a targeted element reference.
```bash
agent-browser get text @e1

```


* **Get Source Code:** Returns the raw HTML snippet of an item.
```bash
agent-browser get html @e1

```


* **Get Input Fields State:** Reads values currently held in text elements.
```bash
agent-browser get value @e2

```


* **Get Attributes:** Pulls explicit DOM attributes like URLs or styles.
```bash
agent-browser get attr @e3 "href"

```


* **Get Page Title:** Returns the current page title.
```bash
agent-browser get title

```


* **Get Current URL:** Returns the current address bar string.
```bash
agent-browser get url

```


* **Check Element Visibility:** Evaluates whether an item is visible in the layout tree.
```bash
agent-browser is visible "#error-message"

```


* **Check Form Element States:** Checks if checkboxes, radio options, or interactive buttons are enabled or checked.
```bash
agent-browser is enabled @e4
agent-browser is checked "#terms-checkbox"

```



---

## 6. Execution Control & Waiting Systems

To prevent race conditions during asynchronous operations or dynamic page rendering, use explicit wait rules.

* **Wait for Element Visibility:** Pauses execution until a target selector enters a visible layout state.
```bash
agent-browser wait "#success-modal"

```


* **Wait for Text Matches:** Suspends execution flow until target text content matches a substring on the page.
```bash
agent-browser wait --text "Transaction Complete"

```


* **Wait for Dynamic URL Routing:** Blocks execution until the active browser URL matches an expression pattern.
```bash
agent-browser wait --url "**/dashboard"

```


* **Wait for Network Idle:** Pauses execution until ongoing background network requests drop to zero (`networkidle`).
```bash
agent-browser wait --load networkidle

```


* **Wait for Element Disappearance:** Pauses execution until a loader or spinner element is completely hidden.
```bash
agent-browser wait "#loading-spinner" --state hidden

```


* **Fixed Time Delays:** Forces a fixed execution delay in milliseconds. Use this sparingly when event-driven waits cannot be applied.
```bash
agent-browser wait 1500

```



---

## 7. Advanced Configurations & State Management

These tools control device state emulation, cookie management, network routes, and cross-application automation.

* **Clipboard Interactions:** Reads, writes, or runs native clipboard operations inside the browser.
```bash
agent-browser clipboard read
agent-browser clipboard write "Sample Injection Text"
agent-browser clipboard copy
agent-browser clipboard paste

```


* **Mouse Coordinate Control:** Drives precision hardware inputs when abstract DOM targets cannot accept standard clicks.
```bash
agent-browser mouse move 450 200
agent-browser mouse down left
agent-browser mouse up left
agent-browser mouse wheel -100

```


* **Viewport & Environment Emulation:** Adjusts rendering viewports, simulates mobile platforms, configures custom HTTP headers, or fakes coordinates.
```bash
agent-browser set viewport 1920 1080 2
agent-browser set device "iPhone 14"
agent-browser set geo 37.7749 -122.4194
agent-browser set offline on
agent-browser set headers '{"Authorization": "Bearer token123"}'

```


* **Cookies & Cache Manipulation:** Directs data stores, imports active state variables from curl data dumps, or deletes stored authentication state.
```bash
agent-browser cookies
agent-browser cookies set session_id "xyz987"
agent-browser cookies set --curl cookies_dump.txt
agent-browser cookies clear

```


* **Network Mocking & Interception:** Alters routing pipelines to block media resources, mock external endpoints, or simulate infrastructure failures.
```bash
agent-browser network route "[https://api.site.com/](https://api.site.com/)*" --body '{"mocked": true}'
agent-browser network route "[https://analytics-tracker.com/](https://analytics-tracker.com/)*" --abort
agent-browser network route '*' --abort --resource-type script
agent-browser network unroute "[https://api.site.com/](https://api.site.com/)*"

```


* **Load Specialized Skills Environment:** Loads workflows tailored to specialized orchestration engines outside standard web pages.
```bash
agent-browser skills get electron       # Destop applications (VS Code, Slack, Figma)
agent-browser skills get slack          # Slack workspace operational automation
agent-browser skills get dogfood        # Exploratory testing/QA bug hunts
agent-browser skills get vercel-sandbox # Sandboxed microVM operations
agent-browser skills get agentcore      # AWS Bedrock AgentCore cloud execution engines

```


* **List Specialized Skills:** Lists all automation modules available on the current local setup.
```bash
agent-browser skills list

```



---

## 8. Troubleshooting Rules

* **Stale Element Reference Error:** If an elements map changes because a page reloads or uses dynamic routing, old references like `@e1` break. When this happens, run `agent-browser snapshot -i` again to generate a new list of interactive references.
* **Element Interception / Not Clickable:** If a modal or dropdown blocks an element, run `agent-browser wait "#overlay" --state hidden` to wait for the overlay to disappear, or click the modal backdrop element first to clear the view.
* **Context Token Optimization:** To keep token usage low, avoid running `agent-browser snapshot` (unfiltered) on large web pages. Instead, use `agent-browser snapshot -i` to filter for interactive elements only.