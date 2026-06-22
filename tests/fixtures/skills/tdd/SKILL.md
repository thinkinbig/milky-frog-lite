---
name: tdd
description: Test-driven development. Use when the user wants to build features or fix bugs test-first, mentions red-green-refactor, or asks for integration tests.
---

# Test-Driven Development

## Philosophy

Tests verify behavior through public interfaces, not implementation details. Good tests read like specifications and survive refactors. Bad tests mock internals, call private methods, or break when you rename code without changing behavior.

**Vertical slices, not horizontal.** Do not write all tests then all code. One failing test → minimal code to pass → repeat.

```
WRONG:  test1, test2, test3  →  impl1, impl2, impl3
RIGHT:  test1 → impl1 → test2 → impl2 → ...
```

## Milky Frog workflow

Before coding, read `CONTEXT.md` in the workspace so test names match domain language (Run, Harness, Tool, …).

1. **Plan** — Confirm with the user which behaviors matter; list them as observable outcomes, not implementation steps.
2. **Tracer bullet** — One test for the first behavior (`uv run pytest tests/...::test_name`), watch it fail, write minimal production code, watch it pass.
3. **Loop** — One test at a time; only enough code for the current test; no speculative features.
4. **Refactor** — Only after green; run `uv run pytest` after each refactor step.

## Checklist per cycle

- Test describes behavior, not implementation
- Test uses public interface only
- Test would survive an internal refactor
- Code is minimal for this test only
