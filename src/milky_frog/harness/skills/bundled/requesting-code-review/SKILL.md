---
name: requesting-code-review
description: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
---

# Requesting Code Review

Review early, review often. Catch issues before they cascade.

**Core principle:** Review after each significant task, not just at the end.

## When to Request Review

**Mandatory:**
- After completing a major feature
- Before merging to main
- After fixing a complex bug

**Valuable:**
- When stuck (fresh perspective)
- Before refactoring (baseline check)

## How to Review

### Step 1: Get the diff

```bash
BASE=$(git rev-parse main)      # or origin/main
HEAD=$(git rev-parse HEAD)
git diff $BASE..$HEAD --stat    # what changed
git diff $BASE..$HEAD           # full diff
```

### Step 2: Systematic review

Check in this order:

1. **Correctness** — does it do what it's supposed to do?
2. **Edge cases** — what happens with empty input, nulls, boundary values?
3. **Error handling** — are errors handled or silently swallowed?
4. **Tests** — do tests actually cover the behavior, or just call the code?
5. **Simplification** — is there a simpler way to express this?

### Step 3: For Milky Frog code, also check

- Domain language (Run, Harness, Tool, Handler — not session/workflow/plugin/middleware)
- Frozen `@dataclass(frozen=True, slots=True)` for value types
- No bare `lambda` in production code
- Three event lanes not unified (Checkpoint / Lifecycle signal / HandlerResult)
- Handlers don't publish lifecycle signals — only `RunEmitter` does
- `RunBeforeStart` is pure observation — no content injection

### Step 4: Act on findings

- **Critical** (breaks correctness, security) — fix immediately before proceeding
- **Important** (missing tests, wrong abstraction) — fix before merge
- **Minor** (style, naming) — note for later or fix quickly

## Red Flags

**Never:**
- Skip review because "it's simple"
- Ignore Critical issues
- Proceed with unfixed Important issues

**If you find something wrong:**
- State it clearly with the file and line
- Explain why it's wrong
- Propose the fix
