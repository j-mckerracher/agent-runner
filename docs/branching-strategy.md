# Branching Strategy: `feature/refactor-to-v1`

## Overview

This document describes the branching strategy for the v1 refactor effort.

## Branch Hierarchy

```
main
└── feature/refactor-to-v1          ← integration branch for the entire refactor
    ├── refactor/<topic-a>           ← individual work branches
    ├── refactor/<topic-b>
    └── refactor/<topic-c>
```

## Workflow

1. **`feature/refactor-to-v1`** is the long-lived integration branch for the full refactor. It is never pushed directly to `main` until the entire refactor is complete and reviewed.

2. **Individual work branches** are cut from `feature/refactor-to-v1`:

   ```bash
   git checkout feature/refactor-to-v1
   git checkout -b refactor/<topic>
   ```

3. Do all work for that topic on the work branch, then commit and merge back into `feature/refactor-to-v1`:

   ```bash
   git checkout feature/refactor-to-v1
   git merge refactor/<topic>
   git branch -d refactor/<topic>   # clean up
   ```

4. Repeat for each subsequent body of work.

5. When the full refactor is complete, open a PR from `feature/refactor-to-v1` → `main`.

## Naming Conventions

| Branch type | Pattern | Example |
|---|---|---|
| Integration branch | `feature/refactor-to-v1` | `feature/refactor-to-v1` |
| Work branches | `refactor/<short-topic>` | `refactor/runner-dispatch`, `refactor/artifact-layout` |

## Keeping Work Branches Up to Date

If `feature/refactor-to-v1` advances while a work branch is in progress, rebase the work branch onto it:

```bash
git checkout refactor/<topic>
git rebase feature/refactor-to-v1
```

## Merging to `main`

Only `feature/refactor-to-v1` is merged to `main` — never individual work branches. This keeps `main`'s history clean and ensures the full refactor lands as a single coherent unit.
