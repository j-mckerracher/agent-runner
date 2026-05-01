---
name: rls-specimen-e2e
description: >
  Guide for running Specimen Accessioning (rls-specimen-accessioning-e2e) end-to-end tests
  locally using Cypress and Nx. Use this skill whenever asked to run, debug, or investigate
  E2E tests for the specimen accessioning application.
---

# Running RLS Specimen Accessioning E2E Tests Locally

## Prerequisites

1. **Chrome browser** is required — all E2E tests must run in Chrome.
2. **Credentials** — tests authenticate against Microsoft AAD. Provide credentials via one of these methods:

   **Option A (recommended for local dev) — `cypress.env.json`:**
   Create the file at `apps/pearls/rls-specimen-accessioning-e2e/cypress.env.json`:

   ```json
   {
     "username": "your-email@mayo.edu",
     "password": "your-password",
     "secret": "BASE32_TOTP_SECRET_IF_MFA_ENABLED"
   }
   ```

   > `secret` is only needed if the test account has MFA enabled.

   **Option B — environment variables:**

   ```bash
   export CYPRESS_USERNAME=your-email@mayo.edu
   export CYPRESS_PASSWORD=your-password
   export CYPRESS_SECRET=BASE32_TOTP_SECRET  # optional, for MFA
   ```

3. **App server** — Cypress auto-starts the dev server (`http://localhost:4201`) via `webServerCommands` in `cypress.config.ts`. You do **not** need to start it manually. If it times out, start it manually first:
   ```bash
   npm run start:spec
   # or: nx serve rls-specimen-accessioning
   ```

---

## Running the Tests

### Interactive (Cypress UI — recommended for local development)

```bash
# Open Cypress Test Runner against local dev server
npm run cy:spec:open:e2e:local

# Equivalent nx command
nx open-cypress rls-specimen-accessioning-e2e --e2e --browser chrome
```

### Headless (CI-style, terminal output)

```bash
# Run all E2E tests headlessly against local dev server
nx e2e rls-specimen-accessioning-e2e --browser chrome

# Skip Nx cache to force a fresh run
nx e2e rls-specimen-accessioning-e2e --browser chrome --skip-nx-cache
```

### Run a Single Spec File

```bash
nx e2e rls-specimen-accessioning-e2e --browser chrome \
  --spec "src/e2e/orders-create.cy.ts"
```

---

## Targeting Different Environments

| Environment     | npm script                       | Base URL                                              |
| --------------- | -------------------------------- | ----------------------------------------------------- |
| Local (default) | `npm run cy:spec:open:e2e:local` | `http://localhost:4201`                               |
| Dev             | `npm run cy:spec:open:e2e:dev`   | `https://specac.rls-ui-dev.mcs-rls-n.caf.mccapp.com`  |
| Test            | `npm run cy:spec:open:e2e:test`  | `https://specac.rls-ui-test.mcs-rls-n.caf.mccapp.com` |

For custom URLs:

```bash
nx open-cypress rls-specimen-accessioning-e2e --e2e --browser chrome \
  --config baseUrl=https://your-url.example.com \
  -e TEST_ENVIRONMENT=dev
```

---

## Running in Docker

```bash
# Build the Cypress Docker image (first time or after dependency changes)
npm run cy:docker:build

# Run all specimen accessioning E2E tests in Docker (headless)
npm run cy:docker:e2e:rls-specimen-accessioning-e2e

# Open Cypress UI via Docker
npm run cy:docker:open:rls-specimen-accessioning-e2e
```

---

## Key Files & Locations

| Path                                                                | Purpose                                                      |
| ------------------------------------------------------------------- | ------------------------------------------------------------ |
| `apps/pearls/rls-specimen-accessioning-e2e/`                        | E2E project root                                             |
| `apps/pearls/rls-specimen-accessioning-e2e/cypress.config.ts`       | Cypress config (base URL, credentials, browser flags)        |
| `apps/pearls/rls-specimen-accessioning-e2e/cypress.env.json`        | Local credentials (gitignored — create manually)             |
| `apps/pearls/rls-specimen-accessioning-e2e/src/e2e/`                | All spec files (`*.cy.ts`)                                   |
| `apps/pearls/rls-specimen-accessioning-e2e/src/e2e/_testData.ts`    | Environment-specific test data/accounts                      |
| `apps/pearls/rls-specimen-accessioning-e2e/src/e2e/_apiUrls.ts`     | API base URLs per environment                                |
| `apps/pearls/rls-specimen-accessioning-e2e/src/support/commands.ts` | Custom Cypress commands (`cy.login`, `cy.createOrder`, etc.) |

### Available Spec Files

| Spec File                     | What It Tests               |
| ----------------------------- | --------------------------- |
| `orders-create.cy.ts`         | Order creation workflows    |
| `orders-update.cy.ts`         | Order update workflows      |
| `orders-display.cy.ts`        | Order display and viewing   |
| `orders-search.cy.ts`         | Order search functionality  |
| `tests-receipting.cy.ts`      | Test receipt workflows      |
| `tests-triaging.cy.ts`        | Test triaging functionality |
| `save-receipt-behavior.cy.ts` | Receipt save behavior       |
| `error-handling.cy.ts`        | Error state handling        |
| `person-id-links.cy.ts`       | Person ID link navigation   |
| `print-button-behavior.cy.ts` | Print button functionality  |
| `triage-account-status.cy.ts` | Triage account status       |

---

## Custom Cypress Commands

These are available inside any spec file:

```typescript
cy.login({ expectedUri: '/' })           // AAD login with MFA support; use in beforeEach
cy.createOrder(orderPayload?)            // Create an order via API
cy.getOrCreateOrder(orderPayload?)       // Get cached or create new order
cy.getSessionItem(key)                   // Read from sessionStorage
cy.setSessionItem(key, value)            // Write to sessionStorage
```

---

## Authentication Details

- Tests use `cy.session()` (cached per spec run) to authenticate against Microsoft AAD
- Handles `login.microsoftonline.com`, `login.live.com`, and `login.mayo.edu`
- If MFA is required, provide the Base32 TOTP `secret` in credentials
- Session is validated by checking for a non-empty user display name element

---

## Test Reporting

Reports are written to `reports/{projectRoot}/` after a headless run:

```bash
# Merge all JUnit XML reports
npm run junit:report-merger

# Generate Mochawesome HTML summary
npm run mochawesome:merge && npm run mochawesome:generate
```

---

## Troubleshooting

| Problem                           | Fix                                                                                                                         |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| App server times out on startup   | Run `npm run start:spec` first, then re-run tests                                                                           |
| Login fails / session invalid     | Check credentials in `cypress.env.json`; ensure the test account is active                                                  |
| MFA prompt blocks login           | Obtain the Base32 TOTP secret for the test account and set `secret` in `cypress.env.json`                                   |
| Nx cache returns stale results    | Add `--skip-nx-cache` flag                                                                                                  |
| Chrome not found                  | Install Chrome; tests **cannot** run in Electron or Firefox                                                                 |
| `cy.login` loops or hangs         | The AAD tenant may be blocking incognito; remove `--incognito` from `cypress.config.ts` `before:browser:launch` temporarily |
| Tests pass locally but fail in CI | CI uses `serve-static` (pre-built assets); run `nx build rls-specimen-accessioning` before CI-style execution               |
