---
name: code-review
description: Review code for correctness bugs, type safety, style violations, and simplification opportunities. Use when the user asks to review, audit, or check code quality, or before merging a change.
---

# Code Review

## Running checks

Before reviewing manually, run all CI checks at once:

```bash
bash .milky-frog/skills/code-review/scripts/run_checks.sh
```

This runs pytest → ruff check → ruff format --check → pyrefly in order, stopping on first failure.

## Order of review

Work through these in order — stop and report at the first blocking issue rather than collecting everything silently.

1. **Correctness** — logic bugs, wrong assumptions, missing edge cases, error paths that lose data
2. **Type safety** — run `uv run pyrefly check` and surface any new errors introduced by the diff
3. **Style** — run `uv run ruff check .` and `uv run ruff format --check .`; report unfixed violations
4. **Tests** — run `uv run pytest`; confirm nothing regresses
5. **Simplification** — dead code, unnecessary abstractions, duplicated logic worth collapsing

## Milky Frog-specific checks

- Domain language: names must match `CONTEXT.md` (Run, Harness, Tool, Handler, Checkpoint — not session, workflow, plugin, middleware)
- Value types must be `@dataclass(frozen=True, slots=True)`; Pydantic only for Checkpoint bodies and lifecycle signals
- No bare `lambda` in production code; use named functions or `stubs.py` in tests
- Seam changes (new `Protocol`, changed adapter) need a note about whether an ADR is warranted
- Three event lanes must stay separate — Checkpoint snapshot, Lifecycle signal, HandlerResult are never merged
- Handlers observe only; loop owns `RunState` evolution
- `RunBeforeStart` is pure observation — no content injection via this event

## Output format

For each finding: **file:line — severity — description**.
Severities: `blocking` (must fix), `warning` (should fix), `nit` (optional).
End with a one-line overall verdict: `LGTM`, `LGTM with nits`, or `Needs changes`.
