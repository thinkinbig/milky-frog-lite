# ADR-0013: Handler design — one type, control-return via the loop

- **Status:** Accepted (2026-07-02)
- **Supersedes:** the `BaseHandler` / `ObserverHandler` / `ControlHandler` /
  `HandlerBundle` hierarchy and the `core/handlers/results.py` fold framework.

> Note: `docs/ARCHITECTURE.md` records that the `docs/adr/` set was retired in
> favour of that living document. This ADR intentionally revives the directory
> for one focused decision; if the team prefers, fold it back into
> `ARCHITECTURE.md` and delete this file. The rationale — not the filename — is
> what matters.

## Context

A Handler is how out-of-loop concerns (checkpointing, observability, UI, and
now transcript compaction) hook into a Run without the `AgentLoop` knowing about
them. Handlers subscribe to **lifecycle signals** broadcast on the `EventHub`.

Compaction introduced the first Handler that needs to *change* the Run, not just
observe it: it must propose replacing the transcript prefix with a summary. The
first implementation modelled this with a type split — `ObserverHandler` vs
`ControlHandler` — plus a generic "fold framework" (`ControlReturn`,
`ControlFold`, `BeforeModelFold`, and a family of `fold_*` reducers in
`core/handlers/results.py`). Each control Handler had to supply its own `fold`
function with an untyped (`object`) accumulator, dispatched through a runtime
`isinstance` check.

Reviewing it against the deep-module lens surfaced three problems:

1. **The abstraction was spread thin.** Four handler types plus a union, none
   deep. A contributor had to learn all of them plus the fold protocol to write
   any Handler.
2. **Huge interface, one adapter of leverage.** The entire fold framework
   existed to serve a single behaviour (`Compacted`), whose application is one
   line: `state = replace(state, compaction=...)`.
3. **A layering inversion.** `core/handlers/results.py` held compaction-specific
   fold logic while the producer (`CompactionHandler`) lived in `harness/`,
   splitting one feature across two packages that already import each other.

## Decision

### One `Handler` type

There is a single `Handler` base in `events/hub.py`:

```python
class Handler:
    def register(self, hub: EventHub) -> None: ...   # attach callbacks
    async def aclose(self) -> None: ...              # release resources, default no-op
    # + async context manager (__aenter__/__aexit__ → aclose)
```

`BaseHandler` / `ObserverHandler` / `ControlHandler` / `HandlerBundle` are gone.

### Observe vs control is the callback's *return*, not a type

Every callback has the shape `async (event, deps) -> HandlerResult | None`:

- return `None` → pure observation (checkpoint, Langfuse, UI);
- return a `HandlerResult` → a proposal for the loop to apply.

`EventHub.broadcast` calls every registered callback in priority-then-
registration order and collects the non-`None` returns:

```python
async def broadcast(self, event) -> list[HandlerResult]:
    results = []
    for reg in self._sorted(...):
        r = await reg.handler(event, deps)
        if r is not None:
            results.append(r)
    return results
```

There is no `EventHub.control(...)`, no per-registration `fold`, and no
observer-return runtime guard — the type `HandlerResult | None` carries the
distinction that the guard used to enforce.

### The loop — never a Handler — owns `RunState` evolution

The `AgentLoop` applies proposals at the relevant control point via a small
reducer it owns (`events/loop.py`):

```python
def _apply_control(state, results):
    for result in results:
        match result:
            case Compacted(compaction):
                state = replace(state, compaction=compaction)
    return state
```

Handlers propose; the loop decides. The generic fold framework is deleted.

### Control-return types live in `domain`

`Compacted` and the `HandlerResult` alias live in `domain/run.py`, next to
`CompactionState`. `domain` depends on nothing, so both the producer
(`harness/compaction.py`) and the consumer (`events/loop.py`) import them
without recreating a `core ↔ harness` cycle. `core/handlers/` keeps only
`HandlerDeps`.

`HandlerDeps` carries **stable, cross-event framework dependencies** — never
per-Run facts. Per-Run data rides on the lifecycle signal itself (e.g.
`RunBeforeModel.state`).

### Construction, registration, lifetime are three separate steps

- **Construct:** most Handlers in `make_session_handlers`
  (`core/runtime/assemble.py`); Handlers needing live resources (`model`,
  `counter`) are built in `AgentSession.__aenter__` and injected via `extra=`.
  The factory returns a `list[Handler]` and never touches the hub.
- **Register:** the session loops `for h in handlers: h.register(hub)`.
- **Lifetime:** the session enters each Handler with `async with`; `aclose`
  releases resources (override it when a Handler holds an HTTP client, etc.).

Ordering is controlled by priority: `hub.on(Event, priority=100)` runs before
lower priorities (`CheckpointHandler` uses 100 so it persists first).

## How to extend

**Add an observer** (the common case):

```python
class MyHandler(Handler):
    def register(self, hub):
        hub.on(RunAfterModel)(self._on_after_model)

    async def _on_after_model(self, event, deps=None):
        ...          # observe; return None
```

**Add a new control-return** (rare — e.g. tool authorization on
`RunBeforeTool`):

1. add a union member to `HandlerResult` in `domain/run.py` (e.g. `Blocked`);
2. add a `case Blocked(...)` to the loop's reducer at that control point;
3. write a `Handler` whose callback returns the new proposal when it fires.

No new fold machinery, reducer, or Handler subtype.

## Invariants

- A Handler never mutates `RunState` directly — it returns a proposal; the loop
  applies it.
- A Handler never publishes lifecycle signals — only `RunEmitter` does.
- Observe-vs-control is the callback's return value, not a separate type.
- Control-return types live in `domain`, not `core` — keep feature knowledge out
  of the inner layer.

## Consequences

- **Smaller surface, matched leverage.** The control-return lane went from a
  ~90-line generic reducer spread across three packages to one `HandlerResult`
  alias plus a ~6-line `_apply_control` in the loop. The seam now costs about
  what its single adapter is worth, and widening it (step "add a new
  control-return") is cheap and local.
- **Locality restored.** Compaction is one feature again: producer, proposal
  type, and applier are no longer smeared across `core`, `harness`, and
  `events`.
- **Weaker runtime guard, stronger static one.** An observer that accidentally
  returns a proposal is no longer caught at runtime; it is a type error under
  the `HandlerResult | None` signature instead.
- **Bet deferred, not taken.** If several request-shaping control points arrive
  (budgeting, `RunBeforeTool` authorization), a generic reducer may re-earn its
  keep — but it should live in `events/` (with the loop), stay feature-agnostic,
  and each feature contributes its own `case`. Revisit only when the second
  adapter is real.

## See also

- `docs/ARCHITECTURE.md` — the three event lanes (do not unify).
- `CLAUDE.md` — "Three event lanes" and the control-return seam notes.
