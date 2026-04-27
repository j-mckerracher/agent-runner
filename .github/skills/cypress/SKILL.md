---
name: cypress
description: >
  Complete Cypress API reference and project-specific testing conventions for the MCS Products Mono UI workspace.
  Use this skill when writing, debugging, or reviewing Cypress component tests, E2E tests, test harnesses, or page objects.
  Keywords: cypress, cy., component test, e2e test, test harness, page object, cy.mount, cy.intercept, cy.visit,
  cy.get, cy.login, data-test-id, getMountOptionsCurry, MountOptionsFn, byTestId, selectByDataTestId,
  assertions, should, intercept, stub, spy, fixture, wait, within, wrap, session.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is a reference and conventions guide, so only add live repo context during a concrete Cypress task.

# Cypress Knowledge

## When to Use This Skill

Activate this skill when:

- Writing or modifying Cypress component tests (`*.cy.ts` in `libs/`)
- Writing or modifying Cypress E2E tests (`*.cy.ts` in `apps/**/*-e2e/`)
- Creating or updating test harnesses (`*.test-harness.ts`)
- Creating or updating E2E page objects (`src/support/*.page.ts`)
- Debugging `cy.*` command chains, assertions, or intercepts
- Setting up `cypress.config.ts` for a new app
- Looking up a specific `cy.*` or `Cypress.*` API

---

## Cypress Command Reference

Cypress commands are enqueued, not executed immediately. They form a chain where each command yields a subject to the next. Never assign `cy.*` return values to variables and use them like synchronous values.

### Queries (Read DOM state — auto-retrying)

| Command                    | Description                                       |
| -------------------------- | ------------------------------------------------- |
| `cy.get(selector)`         | Find element(s) by CSS selector or alias          |
| `cy.contains(text)`        | Find element by text content                      |
| `cy.find(selector)`        | Find descendant within previous subject           |
| `cy.filter(selector)`      | Filter current collection by selector             |
| `cy.eq(index)`             | Select element at index from collection           |
| `cy.first()`               | Select first item in collection                   |
| `cy.last()`                | Select last item in collection                    |
| `cy.children()`            | Get children of current element                   |
| `cy.parent()`              | Get parent element                                |
| `cy.parents()`             | Get all ancestor elements                         |
| `cy.closest(selector)`     | Get first matching ancestor                       |
| `cy.next()`                | Get next sibling                                  |
| `cy.prev()`                | Get previous sibling                              |
| `cy.siblings()`            | Get all sibling elements                          |
| `cy.focused()`             | Get currently focused element                     |
| `cy.root()`                | Get root DOM element                              |
| `cy.shadow()`              | Traverse into shadow DOM                          |
| `cy.document()`            | Get `window.document`                             |
| `cy.window()`              | Get `window` object                               |
| `cy.url()`                 | Get current URL string                            |
| `cy.hash()`                | Get URL hash                                      |
| `cy.title()`               | Get document title                                |
| `cy.location()`            | Get `window.location` object                      |
| `.as(alias)`               | Assign an alias; retrieve with `cy.get('@alias')` |
| `.invoke(fnName, ...args)` | Invoke a function on the subject                  |
| `.its(propertyPath)`       | Get a property value from the subject             |

### Assertions

| Command                       | Description                                                      |
| ----------------------------- | ---------------------------------------------------------------- |
| `.should(assertion, ...args)` | Assert on current subject; auto-retries until passing or timeout |
| `.and(assertion, ...args)`    | Alias for `.should()`; chains multiple assertions                |

**Common assertion strings:**

```typescript
.should('exist')
.should('not.exist')
.should('be.visible')
.should('not.be.visible')
.should('be.disabled')
.should('not.be.disabled')
.should('have.text', 'expected')
.should('contain.text', 'partial')
.should('have.value', 'input value')
.should('have.class', 'my-class')
.should('not.have.class', 'my-class')
.should('have.attr', 'href', 'url')
.should('have.length', 3)
.should('be.empty')
.should('be.checked')
.should('be.calledWithMatch', args)   // for stubs
.should('have.been.called')           // for spies/stubs
```

### Actions (Wait for element actionability before interacting)

| Command               | Description                                          |
| --------------------- | ---------------------------------------------------- |
| `.click()`            | Click a DOM element                                  |
| `.dblclick()`         | Double-click a DOM element                           |
| `.rightclick()`       | Right-click a DOM element                            |
| `.type(text)`         | Type into an input or textarea                       |
| `.clear()`            | Clear an input or textarea value                     |
| `.check()`            | Check a checkbox or radio                            |
| `.uncheck()`          | Uncheck a checkbox                                   |
| `.select(value)`      | Select an `<option>` within `<select>`               |
| `.selectFile(file)`   | Select a file in an `<input type="file">`            |
| `.trigger(event)`     | Trigger a DOM event (e.g. `mouseenter`, `mouseover`) |
| `.scrollIntoView()`   | Scroll element into viewport                         |
| `.scrollTo(position)` | Scroll to a position                                 |

### Other Commands

| Command                                | Description                                            |
| -------------------------------------- | ------------------------------------------------------ |
| `cy.intercept(method, url, response?)` | Spy/stub network requests                              |
| `cy.mount(component, config?)`         | Mount Angular component for component testing          |
| `cy.visit(url)`                        | Navigate to a URL (E2E)                                |
| `cy.wait(aliasOrMs)`                   | Wait for an aliased intercept or a number of ms        |
| `.within(fn)`                          | Scope all subsequent commands to this element          |
| `.wrap(subject)`                       | Yield an arbitrary value as a Cypress chainable        |
| `.then(fn)`                            | Invoke a callback with the current subject             |
| `.each(fn)`                            | Invoke callback on each item in an array               |
| `cy.fixture(filePath)`                 | Load fixture data from `cypress/fixtures/`             |
| `cy.request(options)`                  | Make an HTTP request                                   |
| `cy.session(id, setup, options?)`      | Cache and restore session data (cookies, localStorage) |
| `cy.spy(obj, method)`                  | Wrap a method as a spy                                 |
| `cy.stub(obj, method)`                 | Replace a method with a stub                           |
| `cy.clock()`                           | Override browser time (`Date`, `setTimeout`)           |
| `cy.tick(ms)`                          | Advance mocked time                                    |
| `cy.viewport(width, height)`           | Set viewport size                                      |
| `cy.reload()`                          | Reload the page                                        |
| `cy.go(direction)`                     | Navigate browser history (back/forward)                |
| `cy.exec(command)`                     | Execute a system shell command                         |
| `cy.task(eventName, args?)`            | Execute Node.js code via `task` plugin event           |
| `cy.readFile(path)`                    | Read a file from disk                                  |
| `cy.writeFile(path, content)`          | Write a file to disk                                   |
| `cy.screenshot()`                      | Take a screenshot                                      |
| `cy.log(message)`                      | Print to Cypress Command Log                           |
| `cy.debug()`                           | Set debugger breakpoint                                |
| `cy.pause()`                           | Pause test execution interactively                     |
| `.blur()`                              | Blur a focused element                                 |
| `.focus()`                             | Focus a DOM element                                    |
| `.end()`                               | Explicitly end a command chain                         |
| `.spread(fn)`                          | Invoke callback with multiple arguments                |
| `cy.origin(url, fn)`                   | Execute commands in a different origin                 |
| `cy.clearLocalStorage()`               | Clear localStorage                                     |
| `cy.clearCookies()`                    | Clear browser cookies                                  |
| `cy.clearAllCookies()`                 | Clear all browser cookies across origins               |

---

## Cypress.\* Global API

These execute **immediately** (not enqueued):

| Property/Method                        | Description                                  |
| -------------------------------------- | -------------------------------------------- |
| `Cypress.config(key?, value?)`         | Get or set Cypress configuration             |
| `Cypress.env(key?, value?)`            | Get or set environment variables             |
| `Cypress.browser`                      | Current browser info (name, family, version) |
| `Cypress.isBrowser(name)`              | Check if current browser matches name        |
| `Cypress.Commands.add(name, fn)`       | Register a custom command                    |
| `Cypress.Commands.overwrite(name, fn)` | Overwrite an existing command                |
| `Cypress.log(options)`                 | Write to command log from custom commands    |
| `Cypress.dom`                          | DOM helper utilities                         |
| `Cypress.version`                      | Current Cypress version string               |
| `Cypress.spec`                         | Info about the currently running spec        |
| `Cypress.currentTest`                  | Info about the currently running test        |
| `Cypress.testingType`                  | `"e2e"` or `"component"`                     |
| `Cypress.platform`                     | OS platform (from Node `os.platform()`)      |
| `Cypress.arch`                         | CPU architecture                             |
| `Cypress.session`                      | Session helper methods                       |
| `Cypress.Screenshot.defaults(options)` | Set screenshot defaults                      |
| `Cypress.Keyboard.defaults(options)`   | Set `.type()` defaults                       |

### Built-in Utilities

| Utility             | Description                        |
| ------------------- | ---------------------------------- |
| `Cypress._`         | Lodash library                     |
| `Cypress.sinon`     | Sinon.JS (for spy/stub assertions) |
| `Cypress.Promise`   | Bluebird promise library           |
| `Cypress.Blob`      | Blob utilities                     |
| `Cypress.Buffer`    | Node Buffer polyfill               |
| `Cypress.minimatch` | Glob pattern matching              |
| `Cypress.$`         | jQuery                             |

---

## Node Events (cypress.config.ts `setupNodeEvents`)

Configure in `setupNodeEvents(on, config)`:

| Event                   | When it fires                                  |
| ----------------------- | ---------------------------------------------- |
| `before:run`            | Before a run starts                            |
| `after:run`             | After a run finishes                           |
| `before:spec`           | Before each spec file                          |
| `after:spec`            | After each spec file                           |
| `after:screenshot`      | After a screenshot is taken                    |
| `before:browser:launch` | Before browser launch — modify args/extensions |
| `task`                  | Execute Node.js code from `cy.task()`          |
| `file:preprocessor`     | Transform spec files before running            |

**Common `before:browser:launch` Chrome flags:**

```typescript
on('before:browser:launch', (browser, launchOptions) => {
  if (browser.family === 'chromium' && browser.name !== 'electron') {
    launchOptions.args.push('--auth-server-allowlist=_');
    launchOptions.args.push('--incognito');
    launchOptions.args.push('--window-size=1920,1200');
    // For webcam tests:
    launchOptions.args.push('--use-fake-device-for-media-stream');
    launchOptions.args.push('--use-fake-ui-for-media-stream');
    launchOptions.args.push(`--use-file-for-fake-video-capture=${videoPath}`);
  }
  return launchOptions;
});
```

---

## Component Testing Patterns

Component tests live in `libs/**/*.cy.ts` alongside the component.

### `getMountOptionsCurry` Pattern

Every component spec file should use this pattern for flexible, overridable mount config:

```typescript
import { MyComponent } from './my.component';
import { MountConfig } from '@cypress/angular-signals';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';

type MountOptionsFn = (overrides?: Partial<MyComponent>) => MountConfig<MyComponent>;

const getMountOptionsCurry = (initialValues: Partial<MyComponent> = {}): MountOptionsFn => {
  return (overrides = {}) => ({
    imports: [NoopAnimationsModule],
    providers: [],
    componentProperties: {
      ...initialValues,
      ...overrides
    }
  });
};

describe(MyComponent.name, () => {
  let getMountOptions: MountOptionsFn;

  beforeEach(() => {
    getMountOptions = getMountOptionsCurry({
      label: 'Default Label'
    });
  });

  describe('when rendered', () => {
    beforeEach(() => cy.mount(MyComponent, getMountOptions()));

    it('should show the label', () => {
      // given
      // when
      // then
      cy.get('[data-test-id="my-label"]').should('have.text', 'Default Label');
    });

    it('should render with override', () => {
      // given
      cy.mount(MyComponent, getMountOptions({ label: 'Override' }));
      // when
      // then
      cy.get('[data-test-id="my-label"]').should('have.text', 'Override');
    });
  });
});
```

Key rules:

- `cy.mount()` is called inside `beforeEach` or inside the individual `it` block — never at module scope.
- Use `NoopAnimationsModule` to disable Angular animations in component tests (prevents timing issues).
- Provide `componentProperties` to pass inputs to the component.

### Test Harness Pattern

Every component/feature library must have a test harness file (`*.test-harness.ts`) alongside it.

**Naming:** `<component-name>.component.test-harness.ts` or `<feature-name>.test-harness.ts`

**Structure:**

```typescript
/// <reference types="cypress" />
import { selectByDataTestId } from '@rls/common-testing';

export const myComponentTestHarness = () => {
  // Selectors
  const container = () => cy.get(selectByDataTestId('my-component-container'));
  const submitButton = () => cy.get(selectByDataTestId('my-component-submit-button'));
  const errorText = () => cy.get(selectByDataTestId('my-component-error-text'));

  return {
    container,
    submitButton,
    errorText,
    // Actions
    clickSubmit: () => submitButton().click(),
    // Assertions
    shouldShowError: (text?: string) => {
      if (text) return errorText().should('exist').and('contain', text);
      return errorText().should('exist');
    },
    shouldNotShowError: () => errorText().should('not.exist')
  };
};

export type MyComponentTestHarness = ReturnType<typeof myComponentTestHarness>;
```

**Exporting harnesses** — each library must export harnesses from a `testing.ts` file:

```typescript
// libs/my-domain/features/my-feat/src/testing.ts
export * from './lib/my-feat/my-feat.component.test-harness';
```

**Consuming harnesses** in specs:

```typescript
import { myComponentTestHarness, MyComponentTestHarness } from './my.component.test-harness';

describe(MyComponent.name, () => {
  let harness: MyComponentTestHarness;

  beforeEach(() => {
    harness = myComponentTestHarness();
    cy.mount(MyComponent, getMountOptions());
  });

  it('should submit', () => {
    harness.clickSubmit();
    harness.shouldShowError('Required');
  });
});
```

**Composing harnesses** (feature harness composes UI harnesses):

```typescript
import { searchFormTestHarness } from '@rls/my-domain/ui/search-ui/testing';
import { resultsTableTestHarness } from '@rls/my-domain/ui/results-ui/testing';

export const myFeatTestHarness = () => {
  const searchForm = searchFormTestHarness();
  const resultsTable = resultsTableTestHarness();

  return {
    searchForm,
    resultsTable,
    intercept: {
      getResults: (url: string, response?) => cy.intercept('GET', url, response).as('getResults')
    }
  };
};
```

### Selector Helpers (`@rls/common-testing`)

```typescript
import { byTestId, byPartialTestId, byTestIdWithChild, selectByDataTestId, selectByPartialDataTestId, selectByDataTestIdWithChild, byAriaLabel, selectByAriaLabel } from '@rls/common-testing';

byTestId('my-button'); // [data-test-id=my-button]
byPartialTestId('my-'); // [data-test-id*=my-]
byTestIdWithChild('form', 'button'); // [data-test-id=form] button
byAriaLabel('Close'); // [aria-label="Close"]

// All `select*` variants are aliases for the `by*` variants.
```

**`data-test-id` naming conventions:**

- Use kebab-case: `data-test-id="my-component-submit-button"`
- Always prefix with the component tag name: `data-test-id="order-search-form-submit"`
- Every interactive and observable element should have a `data-test-id`

### Running Component Tests

```bash
nx component-test <project-name> --browser=chrome

# Skip Nx cache
nx component-test <project-name> --browser=chrome --skip-nx-cache

# Run specific spec file
nx component-test <project-name> --browser=chrome --spec "src/lib/my-component/my.component.cy.ts"

# CI vs dev spec patterns
# Uses getMergedTestsPatterns(isCI) from @rls/cypress-testing
```

---

## E2E Testing Patterns

E2E tests live in `apps/<product>/<app-name>-e2e/`.

### Directory Layout

```
apps/pearls/my-app-e2e/
├── cypress.config.ts          # Nx-preset E2E config
├── src/
│   ├── e2e/
│   │   ├── _testData.ts       # Typed test data per environment
│   │   ├── _apiUrls.ts        # API base URLs per environment
│   │   ├── authentication.cy.ts
│   │   └── my-feature.cy.ts
│   └── support/
│       ├── commands.ts        # Custom command declarations
│       ├── e2e.ts             # Support file (auto-loaded)
│       └── my-feature.page.ts # Page object
```

### `cypress.config.ts` — Standard E2E Config

```typescript
import { nxE2EPreset } from '@nx/cypress/plugins/cypress-preset';
import { defineConfig } from 'cypress';

const preset = nxE2EPreset(__filename, {
  cypressDir: 'src',
  webServerCommands: {
    default: 'nx run my-app:serve:development --host 127.0.0.1 --port 4201',
    production: 'nx run my-app:serve:production --host 127.0.0.1'
  },
  ciWebServerCommand: 'nx run my-app:serve-static',
  webServerConfig: { timeout: 120000 }
});

export default defineConfig({
  projectId: 'my-app-e2e',
  e2e: {
    ...preset,
    retries: { runMode: 2, openMode: 0 },
    video: true,
    baseUrl: 'http://localhost:4201',
    experimentalModifyObstructiveThirdPartyCode: true,
    injectDocumentDomain: true,
    async setupNodeEvents(on, config) {
      await preset.setupNodeEvents(on, config);
      require('cypress-terminal-report/src/installLogsPrinter')(on, {
        printLogsToConsole: 'onFail'
      });
      on('before:browser:launch', (browser, launchOptions) => {
        if (browser.family === 'chromium' && browser.name !== 'electron') {
          launchOptions.args.push('--auth-server-allowlist=_');
          launchOptions.args.push('--incognito');
          launchOptions.args.push('--window-size=1920,1200');
        }
        return launchOptions;
      });
      require('@cypress/code-coverage/task')(on, config);
      return config;
    }
  }
});
```

### Support File (`src/support/e2e.ts`)

Every E2E app has this standard support file:

```typescript
import './commands';
import '@angular/compiler';
import '@rls/cypress-testing';

Cypress.on('uncaught:exception', (err) => {
  // Return false to prevent Cypress from failing the test on known safe errors
  return !err.message.includes('The play() request was interrupted') && !err.message.includes('Not logged in');
});
```

### Environment Management

**`src/e2e/_testData.ts`** — typed test data per environment:

```typescript
export type Environment = 'ci' | 'dev' | 'test' | 'stage' | 'pen';

export interface MyTestData {
  orderNumber: string;
  orderGuid: string;
}

export type EnvironmentData = Record<Environment, MyTestData>;

export const envTestData: EnvironmentData = {
  ci: { orderNumber: 'D000001', orderGuid: 'guid-1' },
  dev: { orderNumber: 'D000001', orderGuid: 'guid-1' },
  test: { orderNumber: 'T000001', orderGuid: 'guid-2' },
  stage: { orderNumber: 'S000001', orderGuid: 'guid-3' },
  pen: { orderNumber: '', orderGuid: '' }
} as const satisfies EnvironmentData;
```

**`src/e2e/_apiUrls.ts`** — environment-keyed API base URLs:

```typescript
type Dictionary = { [key: string]: string };

export const myApiUrls: Dictionary = {
  ci: 'https://dev.example.com/api',
  dev: 'https://dev.example.com/api',
  test: 'https://test.example.com/api',
  stage: 'https://stage.example.com/api',
  prod: 'https://prod.example.com/api'
};
```

**Reading the environment in a spec:**

```typescript
const testEnv = (Cypress.env('TEST_ENVIRONMENT') || 'ci') as Environment;
const apiUrl = myApiUrls[testEnv];
const testData = envTestData[testEnv];
```

### Page Object Pattern

Page objects live in `src/support/*.page.ts`. They **compose** lib-level test harnesses and add navigation.

```typescript
import { myFeatureTestHarness } from '@rls/my-domain/features/my-feat/testing';
import { CommonPage, commonPage, convertClassToObject } from '@rls/common-testing';

export const myFeaturePage = () => {
  const myFeature = myFeatureTestHarness();
  const tempCommonPage = convertClassToObject<typeof CommonPage>(commonPage);

  const actions = {
    visitPage: () => cy.visit('/my-feature')
  };

  return {
    ...myFeature,
    ...tempCommonPage,
    ...actions
  };
};

export type MyFeaturePage = ReturnType<typeof myFeaturePage>;
```

**Key rules:**

- Create page once: `const page = myFeaturePage()` (at describe scope, not in `beforeEach`)
- Compose by spreading harnesses (`...tableHarness`, `...searchHarness`)
- Always include `convertClassToObject<typeof CommonPage>(commonPage)` for shared page assertions
- Add `cy.visit()` as a named action on the returned object

### E2E Spec Structure

```typescript
import { myFeaturePage, MyFeaturePage } from '../support/my-feature.page';
import { myApiUrls } from './_apiUrls';
import { envTestData, Environment } from './_testData';

const testEnv = (Cypress.env('TEST_ENVIRONMENT') || 'ci') as Environment;
const apiUrl = myApiUrls[testEnv];
const testData = envTestData[testEnv];
const page = myFeaturePage();

describe('My Feature', () => {
  before(() => {
    // eslint-disable-next-line cypress/no-unnecessary-waiting
    cy.wait(4000); // timing buffer for pipeline startup
  });

  beforeEach(() => {
    cy.intercept('GET', `${apiUrl}/v1/items`, {
      statusCode: 200,
      body: []
    }).as('getItems');

    cy.login(); // Always before cy.visit()
    page.visitPage();
    cy.wait('@getItems');
  });

  it('should display the page title', () => {
    // given
    // when
    // then
    page.title().should('contain.text', 'My Feature');
  });
});
```

**CRITICAL order:** `cy.login()` MUST come before `cy.visit()`.

### `cy.login()` Custom Command

Available in all E2E apps via `@rls/cypress-testing`:

```typescript
cy.login(); // Login with env credentials
cy.login({ expectedUri: '/' }); // Login and assert on expected URL after auth
```

The command uses `cy.session()` internally to cache AAD authentication state across tests.

### Running E2E Tests

```bash
# Run all E2E tests
nx e2e <app-name>-e2e --browser=chrome

# Run a single spec file
nx e2e <app-name>-e2e --browser=chrome --spec "src/e2e/my-feature.cy.ts"

# Target a specific environment
nx e2e <app-name>-e2e --browser=chrome -e TEST_ENVIRONMENT=dev

# Skip Nx cache
nx e2e <app-name>-e2e --browser=chrome --skip-nx-cache

# Open Cypress UI (interactive)
nx open-cypress <app-name>-e2e --e2e --browser chrome

# Open against a specific environment
nx open-cypress <app-name>-e2e --e2e --browser chrome \
  --config baseUrl=https://your-env.example.com \
  -e TEST_ENVIRONMENT=test
```

### Available E2E Apps

| App                   | Project Name                      | Port   |
| --------------------- | --------------------------------- | ------ |
| Accession Portal      | `rls-accession-portal-ui-e2e`     | 4202   |
| Specimen Accessioning | `rls-specimen-accessioning-e2e`   | (auto) |
| Order System Viewer   | `rls-order-system-viewer-app-e2e` | (auto) |
| Sendouts              | `rls-sendouts-ui-e2e`             | (auto) |
| Core Home             | `rls-core-home-app-e2e`           | (auto) |
| Core Admin            | `rls-core-admin-app-e2e`          | (auto) |
| Product Catalog       | `prodcat-nexus-app-e2e`           | 4205   |

---

## Assertions Quick Reference

```typescript
// Existence
.should('exist')
.should('not.exist')

// Visibility
.should('be.visible')
.should('not.be.visible')

// State
.should('be.disabled')
.should('not.be.disabled')
.should('be.checked')
.should('be.empty')

// Text content
.should('have.text', 'exact text')
.should('contain.text', 'partial')

// Value
.should('have.value', 'input value')

// CSS
.should('have.class', 'active')
.should('not.have.class', 'active')

// Attributes
.should('have.attr', 'href', '/path')
.should('have.attr', 'aria-label', 'Close')

// Length
.should('have.length', 5)
.should('have.length.greaterThan', 0)
.should('have.length.lessThan', 10)

// Multiple assertions (chain with .and)
.should('exist').and('be.visible').and('contain.text', 'Hello')

// Spy/stub assertions
.should('have.been.called')
.should('have.been.calledWith', arg1, arg2)
.should('be.calledWithMatch', expectedValue)
.should('have.been.calledOnce')
```

---

## Intercept Patterns

### Stub a response

```typescript
cy.intercept('GET', '/api/v1/orders', {
  statusCode: 200,
  body: [{ id: 1, name: 'Order 1' }]
}).as('getOrders');
```

### Use glob patterns for dynamic URLs

```typescript
cy.intercept('GET', '/api/v1/orders/*').as('getOrder');
cy.intercept('POST', '/api/v1/orders?specimenNumber=*').as('createOrder');
```

### Stub with a fixture file

```typescript
cy.intercept('GET', '/api/v1/items', { fixture: 'items.json' }).as('getItems');
```

### Spy without stubbing (let real request through)

```typescript
cy.intercept('GET', '/api/v1/orders').as('getOrders');
cy.wait('@getOrders').its('response.statusCode').should('eq', 200);
```

### Wait for an aliased intercept

```typescript
cy.wait('@getOrders'); // just wait
cy.wait('@getOrders').its('response.body').should('have.length', 3);
```

### Simulate error responses

```typescript
cy.intercept('GET', '/api/v1/orders', {
  statusCode: 500,
  body: { message: 'Internal Server Error' }
}).as('getOrdersError');
```

---

## Common Gotchas

| Problem                          | Rule                                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------ |
| Storing `cy.*` return value      | **Never** — commands are enqueued, not synchronous                                   |
| Numeric `cy.wait(5000)`          | Anti-pattern — use aliased intercepts with `cy.wait('@alias')` instead               |
| `cy.visit()` before `cy.login()` | **Always** call `cy.login()` first in E2E tests                                      |
| `cy.mount()` at module scope     | Must be inside `it()`, `beforeEach()`, or `before()`                                 |
| Missing `NoopAnimationsModule`   | Add to `imports` in `getMountOptionsCurry` to prevent animation timing issues        |
| PrimeNG components not rendering | Run `npm run monkey-patch-primeng` to apply the PrimeNG compatibility patch          |
| Tests pass locally, fail in CI   | CI uses `serve-static` — run `nx build <app>` before CI-style runs                   |
| Stale Nx cache                   | Add `--skip-nx-cache` or run `nx reset`                                              |
| Chrome not available             | All Cypress tests in this project require Chrome — never use Electron or Firefox     |
| `cy.origin()` needed             | Required when a test navigates to a different origin (e.g., AAD login pages)         |
| Non-retrying DOM assertions      | Prefer queries (`.get()`, `.find()`) over non-retrying commands for DOM state checks |
