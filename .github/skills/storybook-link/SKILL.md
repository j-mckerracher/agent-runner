---
name: storybook
description: |
  Storybook Links addon quick reference for navigating between stories.
  Covers setup and core APIs: linkTo, hrefTo, withLinks, and LinkTo (React).
  Use when building interactive story demos or cross-story navigation flows.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It is a static API reference, so live shell output adds little to the shared baseline guidance.

# Storybook Links Addon

Use `@storybook/addon-links` to navigate between stories without full page reloads.

## When to Use This Skill

Activate this skill when:

- A story action should open another story
- You need a URL to a story (`hrefTo`)
- You want declarative link attributes in markup (`withLinks`)
- You are in React and want an anchor-like component (`LinkTo`)

## Core Rule

Prefer built-in Links addon APIs (`linkTo`, `hrefTo`, `withLinks`, `LinkTo`) before creating custom navigation logic.

## Setup

Install and register the addon.

```bash
yarn add -D @storybook/addon-links
```

```ts
// .storybook/main.ts (or main.js)
export default {
  addons: ['@storybook/addon-links']
};
```

## APIs

### `linkTo`

Returns an event handler that navigates to another story.

```ts
import { linkTo } from '@storybook/addon-links';

linkTo('Toggle', 'off');
linkTo('Toggle'); // first story in the "Toggle" group
linkTo(
  () => 'Toggle',
  () => 'off'
);
```

- First arg: story group/title (`kind`)
- Second arg (optional): story export name (`story`)
- Args can be functions that receive event args and return strings

### `hrefTo`

Returns a `Promise<string>` with a relative URL to a target story.

```ts
import { hrefTo } from '@storybook/addon-links';

hrefTo('Href', 'log').then((url) => {
  console.log(url);
});
```

### `withLinks`

Decorator for declarative links via data attributes.

```ts
import { withLinks } from '@storybook/addon-links';

export default {
  title: 'Button',
  decorators: [withLinks]
};
```

```tsx
<button data-sb-kind="OtherKind" data-sb-story="otherStory">
  Go to "OtherStory"
</button>
```

### `LinkTo` (React)

React component from `@storybook/addon-links/react` that behaves like an anchor and supports story navigation.

```tsx
import LinkTo from '@storybook/addon-links/react';

<LinkTo kind="Toggle" story="off" target="_blank" title="Go to off story">
  Go to Off
</LinkTo>;
```

- Accepts standard `<a>` props plus `kind` and `story`
- If `kind` is omitted, current story group is used

## Examples

```tsx
import { linkTo } from '@storybook/addon-links';
import LinkTo from '@storybook/addon-links/react';

export default { title: 'Button' };

export const first = () => <button onClick={linkTo('Button', 'second')}>Go to Second</button>;

export const second = () => <LinkTo story="first">Go to First</LinkTo>;
```

```tsx
import { linkTo } from '@storybook/addon-links';

export default { title: 'Select' };

export const index = () => (
  <select onChange={linkTo('Select', (e) => e.currentTarget.value)}>
    <option>index</option>
    <option>first</option>
    <option>second</option>
    <option>third</option>
  </select>
);
```

## Troubleshooting and Best Practices

- Match `title` and story export names exactly; mismatches cause failed navigation
- Use `hrefTo` when you need URLs (analytics, copying links, custom anchor flows)
- Use `LinkTo` only in React stories; use `linkTo` for framework-agnostic handlers
- Prefer addon APIs over manual `window.location` changes for Storybook navigation
- Keep story names stable to avoid breaking links across docs and tests
