---
name: debug
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
---

# Systematic Debugging

## Overview

Random fixes waste time and create new bugs.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

**Violating the letter of this process is violating the spirit of debugging.**

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## The Four Phases

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read error messages carefully** — stack traces completely, line numbers, error codes
2. **Reproduce consistently** — can you trigger it reliably? What are the exact steps?
3. **Check recent changes** — git diff, recent commits, new dependencies, config changes
4. **Gather evidence in multi-component systems** — add diagnostic instrumentation, log what enters and exits each component boundary, then run once to see WHERE it breaks
5. **Trace data flow** — where does the bad value originate? Keep tracing up until you find the source. Fix at source, not symptom.

### Phase 2: Pattern Analysis

1. **Find working examples** — locate similar working code in the codebase
2. **Compare against references** — read reference implementation completely
3. **Identify differences** — list every difference, however small
4. **Understand dependencies** — what does this need? What assumptions does it make?

### Phase 3: Hypothesis and Testing

1. **Form single hypothesis** — "I think X is the root cause because Y"
2. **Test minimally** — the SMALLEST possible change to test the hypothesis
3. **One variable at a time** — don't fix multiple things at once
4. **Verify before continuing** — did it work? If not, form a NEW hypothesis

### Phase 4: Implementation

1. **Create failing test case** — simplest possible reproduction, automated if possible
2. **Implement single fix** — address the root cause only, no "while I'm here" improvements
3. **Verify fix** — test passes? No other tests broken?
4. **If fix doesn't work** — count how many fixes you've tried:
   - < 3: Return to Phase 1, re-analyze with new information
   - ≥ 3: **STOP — question the architecture** (see below)

### If 3+ Fixes Failed: Question the Architecture

Pattern indicating architectural problem:
- Each fix reveals new shared state / coupling / problem in different place
- Fixes require massive refactoring
- Each fix creates new symptoms elsewhere

**Discuss with the user before attempting more fixes.** This is not a failed hypothesis — this is a wrong architecture.

## Milky Frog-Specific Heuristics

- **Async/cancellation**: check `RunCancellation.is_cancelled` polling and `asyncio.CancelledError` propagation in `events/loop.py`
- **Resume failures**: inspect `checkpoint/snapshot.py` serialization and `harness/state.py::repair_transcript` — most common cause is an unmatched tool call in the transcript
- **Event ordering bugs**: the three lanes (Checkpoint / Lifecycle signal / HandlerResult) must never be confused; determine which lane the symptom belongs to before tracing
- **Handler side-effects**: Handlers must not mutate `RunState` directly — only return `HandlerResult`; unexpected state changes usually mean a Handler is doing something the loop should own
- **Tool execution failures**: check `events/tool_step.py` and the sandbox deny-list in `adapters/local/sandbox.py`

## Red Flags — Return to Phase 1

If you catch yourself thinking:
- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- "One more fix attempt" (when already tried 2+)
- Each fix reveals a new problem in a different place

**ALL of these mean: STOP. Return to Phase 1.**

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Issue is simple, don't need process" | Simple issues have root causes too. |
| "Emergency, no time for process" | Systematic debugging is FASTER than guess-and-check. |
| "Just try this first, then investigate" | First fix sets the pattern. Do it right from the start. |
| "I see the problem, let me fix it" | Seeing symptoms ≠ understanding root cause. |
| "One more fix attempt" (after 2+) | 3+ failures = architectural problem. |

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY |
| **2. Pattern** | Find working examples, compare | Identify differences |
| **3. Hypothesis** | Form theory, test minimally | Confirmed or new hypothesis |
| **4. Implementation** | Create test, fix, verify | Bug resolved, tests pass |
