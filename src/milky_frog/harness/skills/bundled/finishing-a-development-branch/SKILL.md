---
name: finishing-a-development-branch
description: Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion by presenting structured options for merge, PR, or cleanup
---

# Finishing a Development Branch

## Overview

Guide completion of development work by presenting clear options and handling the chosen workflow.

**Core principle:** Verify tests → Present options → Execute choice.

**Announce at start:** "I'm using the finishing-a-development-branch skill to complete this work."

## The Process

### Step 1: Verify Tests

Before presenting options, verify tests pass:

```bash
uv run pytest          # Python / Milky Frog
npm test               # Node.js
cargo test             # Rust
go test ./...          # Go
```

**If tests fail:**
```
Tests failing (N failures). Must fix before completing:

[Show failures]

Cannot proceed until tests pass.
```

Stop. Fix. Then continue to Step 2.

### Step 2: Present Options

```
Implementation complete. What would you like to do?

1. Merge to main locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)
4. Discard this work

Which option?
```

Don't add explanation — keep options concise.

### Step 3: Execute Choice

#### Option 1: Merge Locally

```bash
git checkout main
git pull
git merge <feature-branch>
```

Verify tests on merged result, then delete branch:
```bash
git branch -d <feature-branch>
```

#### Option 2: Push and Create PR

```bash
git push -u origin <feature-branch>
```

Then create PR. Keep branch alive for iteration on feedback.

#### Option 3: Keep As-Is

Report: "Keeping branch `<name>`. You can resume it later."

#### Option 4: Discard

**Confirm first:**
```
This will permanently delete:
- Branch <name>
- All commits: <commit-list>

Type 'discard' to confirm.
```

Wait for exact confirmation, then:
```bash
git checkout main
git branch -D <feature-branch>
```

## Red Flags

**Never:**
- Proceed with failing tests
- Merge without verifying tests on merged result
- Delete work without typed confirmation
- Force-push without explicit request
