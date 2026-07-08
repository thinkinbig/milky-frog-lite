# Architecture

This document describes how Milky Frog (奶蛙) is built **as it stands today**. It
is a living description of the system, not a decision log — when the code and
this document disagree, the code wins and this document is wrong. For the
product vocabulary, see [`CONTEXT.md`](../CONTEXT.md); for day-to-day working
rules, see [`CLAUDE.md`](../CLAUDE.md).

> The `docs/adr/` decision records were retired. Their rationale is folded into
> the "Why" notes throughout this document; their dated history remains in git.

---

## 1. Design stance

Milky Frog is a local coding agent that completes **one user goal at a time**.
Four commitments explain almost every structural choice below; read these first
and the rest of the architecture follows.

- **One foreground Run.** The runtime advances a single Run to a terminal state
  before starting another. There is no scheduler, no work queue, no background
  fan-out. This keeps state reasoning tractable: at any moment there is exactly
  one transcript growing.

- **A linear model → tool → model loop.** A Run is a bounded loop: ask the
  model, run the tools it requested, feed the results back, repeat — until the
  model stops calling tools or a call budget is hit. No graph engine, no
  planner DAG. The loop lives in one readable function (`AgentLoop.advance`).

- **A local policy boundary, not host isolation.** Tool calls pass through a
  `Sandbox` that denies sensitive paths and gates shell commands behind
  approval. This is a *policy* — it constrains a cooperating model, it does not
  contain hostile code. Real isolation (containers) is a future swap of one
  seam, not a property we claim today.

- **Resumable by snapshot.** After each meaningful step the full `RunState` is
  serialized to SQLite. A Run can be resumed by loading that snapshot,
  repairing any tool that was interrupted mid-flight, and continuing — or
  continued with a brand-new user turn. Resume folds a snapshot, it does not
  replay an event log.

> **Why linear and single-Run?** The agent's hard problems are correctness and
> recoverability, not throughput. A linear loop with a durable snapshot after
> every step means an interrupted process loses at most the in-flight model
> call, and the entire history of a Run is a single serializable value.

---

## 2. Layered structure

The package layout under `src/milky_frog/` is a dependency hierarchy. Arrows
point in the direction of allowed dependencies — lower layers never import
higher ones.

```
        cli/            ui/              ← surface (Typer commands, Rich/Textual)
          └──────┬───────┘
                 ▼
               app/                       ← session assembly + sync↔async boundary
                 ▼
   ┌─────────── core/ ───────────┐        ← orchestration policy + protocols
   │ controller  runtime/        │           (controller, runtime/{assemble,
   │ policy      session_tool_…  │            foreground, checkpoint, execute_tool},
   │ sandbox(proto) handlers/    │            sandbox & handler protocols)
   └──────┬──────────────┬───────┘
          ▼              ▼
     adapters/      harness/  events/      ← concrete impls + the running machinery
     local/         tools/    loop.py         (LocalSandbox, OpenAIModel, sqlite;
     models/        state.py  hub.py           the harness, tools, state, the loop,
     checkpoint/    skills/   emitter.py       the bus, tool-step execution)
                    prompt.py tool_step.py
                              events.py
                 ▼
              domain/                        ← shared vocabulary (frozen dataclasses)
```

| Layer | Packages | Responsibility |
|-------|----------|----------------|
| **Surface** | `cli/`, `ui/` | Typer command surface; Rich/Textual presentation. |
| **Assembly** | `app/` | `AgentSession` builds the concrete stack from `Settings` and owns the sync→async boundary (one reused event loop). |
| **Core** | `core/` | Orchestration and the protocol seams: `RunController` (resume/attach parsing), `runtime/` (wiring + foreground driver + checkpoint facade + cancellable tool execution), `Sandbox`/handler protocols, `SessionToolPolicy`. |
| **Adapters** | `adapters/` | Default implementations behind the core protocols: `LocalSandbox`, `OpenAIModel`, sqlite checkpoint store. |
| **Machinery** | `harness/`, `events/` | The actual runtime: `AgentHarness`, `AgentLoop`, `EventHub`/`RunEmitter`, `ToolStepExecutor`, transcript mutators, built-in tools, skills, prompt + token budgeting. |
| **Handlers** | `handlers/` | Concrete lifecycle handler bundles (`CheckpointHandler`, `LangfuseHandler`). |
| **Vocabulary** | `domain/` | Frozen dataclasses and enums every layer shares (`RunState`, `Message`, `ToolCall`, `RunRequest`, `RunResult`, `RunStatus`, …). |

> **Why the split.** `core/` was layered *on top of* the existing `harness/` and
> `events/` machinery rather than replacing it. The point of `core/runtime/` is
> that all wiring lives in one place (`assemble.py`) — `AgentHarness` and
> `AgentLoop` stay free of construction logic, so tests and `app/` build the
> exact same stack the same way. `domain.py` was split into the `domain/`
> package so the vocabulary can grow without one giant module.

The single wiring entry point is `core/runtime/assemble.py`:

- `make_agent_harness(...)` builds the runtime stack — `ToolRegistry` →
  `SessionToolPolicy` → `ToolStepExecutor` → `AgentLoop` (with the model wrapped
  in `RetryingModel`) → `AgentHarness` — and is shared verbatim by
  `AgentSession` and the tests.
- `make_session_handlers(...)` assembles the lifecycle handler list in one
  place: `CheckpointHandler` (priority 100, so it persists before any other
  observer) plus any extras plus an optional `LangfuseHandler`. The caller
  registers each on the hub and owns its lifetime.

---

## 3. The Run lifecycle

A Run is driven by two collaborators with a clean division of labor:

- **`AgentHarness`** (`harness/harness.py`) is a thin coordinator. It *prepares*
  `RunState` — seeds a fresh transcript, repairs an interrupted one on resume,
  resolves a pending tool approval — and then hands off.
- **`AgentLoop`** (`events/loop.py`) is the pure model → tool → model loop. It
  knows nothing about checkpoints or config; it only advances state and
  publishes signals on the `EventHub`.

```
AgentHarness.run(request)
  claim(run_id)                         ← exclusive lock on the Run
  create_run + load ContextLoader section
  hub.run_before_start / start_run / hub.run_started
  budget.init_for_workspace
  └─► AgentLoop.advance(state, sandbox, max_calls, budget):
        while completed_model_calls < max_calls:
          hub.turn_started ; hub.before_model
          request = budget.trim(request)         ← per-call request shaping
          response = model.stream(request)       ← TextDelta / ReasoningDelta / StreamDone
          state = append_model_response(...)
          hub.after_model
          if no tool_calls:  → hub.finish_completed(...)   ← terminal
          for call in response.tool_calls:
            outcome = tool_step.run_with_policy(...)        ← inline authorization
            if outcome is a RunResult:  → return it         ← e.g. approval needed
            state = append_tool_result(...) ; hub.after_tool
          hub.turn_ended
        → hub.finish_paused(max_calls)                      ← budget exhausted
```

The loop's terminal outcomes are all funneled through the hub:
`finish_completed`, `finish_paused` (call budget hit), `finish_cancelled`,
`finish_failed`, and `finish_approval_needed` (a tool requires user approval).
Each returns a `RunResult` and emits the matching terminal lifecycle signal.

**Persistence is not in the loop.** `AgentLoop` never touches the checkpoint
store. Instead `CheckpointHandler` subscribes to lifecycle signals on the hub
and writes `RunState` at the durable boundaries below. This is what lets the
loop stay a pure function of state.

### Emission matrix

The harness/loop alone decide what gets persisted versus merely notified — not
every step does both.

| Step | Checkpoint (`save_state`) | Lifecycle signal |
|------|---------------------------|------------------|
| Run start | ✓ (seeded transcript) | `RunBeforeStart`, `RunStarted` |
| Before each model call | — | `RunTurnStart`, `RunBeforeModel` |
| Streaming | — | `RunModelChunk`, `RunModelReasoning` |
| After model | ✓ | `RunAfterModel` |
| After tool | ✓ | `RunBeforeTool`, `RunAfterTool` |
| Turn end | — | `RunTurnEnd` |
| Run terminal | ✓ + status | `RunCompleted` / `RunPaused` / `RunCancelled` / `RunFailed` |
| In-run notice | — | `RunNotice` |

---

## 4. The three lanes

Milky Frog deliberately keeps three different "things that flow through the
runtime" separate. They are easy to conflate because all three are reactions to
a Run progressing — but they have different lifetimes, owners, and purposes.
**Never call all three "Event" without a qualifier, and never merge them into
one base type.**

| Lane | Where | Lifetime | Purpose |
|------|-------|----------|---------|
| **Checkpoint snapshot** | `checkpoint/snapshot.py`, `runs.state_json` | Durable (SQLite) | The source of truth for resume. |
| **Lifecycle signal** | `events/events.py`, broadcast by `EventHub` | Ephemeral (in-process) | Live UI streaming and observability (Langfuse). |
| **Handler control return** | `HandlerResult` in `core/handlers/results.py` | Per-step | Reserved seam for future per-step authorization. |

**Checkpoint snapshots** are Pydantic models serialized onto the `runs` row.
They are the only durable record of a Run and the only thing resume reads.

**Lifecycle signals** are frozen dataclasses (`events/events.py`) published
*only* by `RunEmitter` (reachable via `EventHub`'s typed publish methods).
Handlers subscribe with `observe` / `on` / `subscribe`. The full set is the
sixteen types in `LIFECYCLE_EVENT_TYPES` — from `RunBeforeStart` through the
four terminal signals plus `RunNotice`. Handlers are pure observers: they react
(render a chunk, send a trace, persist a snapshot) and return `None`.

**Handler control return is currently dormant — and that is the honest state.**
`type HandlerResult = Never`: there are no variants defined. `EventHub.broadcast`
already collects any non-`None` handler returns into a list and hands them back
to the publisher, but since handlers only ever return `None`, that list is
always empty. The plumbing exists as infrastructure for a future seam (e.g. a
handler vetoing or rewriting a step); nothing uses it today.

> **Where authorization actually lives.** Because the control-return lane is
> dormant, tool authorization is enforced **inline**: `ToolStepExecutor`
> consults `SessionToolPolicy` *before* a tool runs and can short-circuit to a
> `finish_approval_needed` result. `RunBeforeTool` and `RunBeforeStart` are pure
> observation — they cannot inject content or block a step. (CONTEXT.md still
> describes these as able to "return control results"; that is aspirational —
> trust the code.)

> **Why keep three lanes if one is empty?** The snapshot/signal split is load-
> bearing today (durable truth vs. ephemeral notification must not mix). The
> control-return lane is kept as a named, typed placeholder so that adding real
> per-step control later is a localized change with an obvious home, rather than
> a retrofit that tempts someone to overload lifecycle signals with control
> semantics.

### Handler bundles and lifetime

Handlers are grouped into **bundles** (`BaseHandler`, `events/hub.py`). A bundle
wires several callbacks onto the hub in one place via `register(hub)`, and may
hold session resources: it is an async context manager, so `AgentSession`
enters every bundle on session open and releases it (`aclose`) on close.
`CheckpointHandler` registers at priority 100 so it persists before other
observers regardless of registration order.

---

## 5. Persistence and resume

State is persisted through the `CheckpointStore` protocol (`checkpoint/`),
default adapter `SqliteCheckpointStore`. The serialized body is a versioned
`RunSnapshot` (`checkpoint/snapshot.py`) holding the whole `RunState` —
messages, token accounting, and the reasoning log.

A Run is **claimed** before any mutation (`checkpoints.claim(run_id)`), giving a
single writer an exclusive lock and turning a concurrent claim into a
`ResumeError`. Three entry points advance an existing Run:

- **`resume(run_id, prompt=None)`** — load the snapshot, `seal` any tool that
  was interrupted mid-flight (so the transcript is well-formed), optionally
  `append_user_message(prompt)` to continue the Run with a new turn, then
  advance. This is what lets the interactive loop keep one growing transcript
  across many prompts.
- **`respond_approval(run_id, approval)`** — release a Run paused on
  `WAITING_FOR_APPROVAL` with the user's verdict. `_apply_approvals` walks the
  unmatched tool calls, resolves each through `ToolStepExecutor`, appends the
  results, and resumes the loop.
- **Repair on load** — `unmatched_tool_calls` finds calls with no result (a
  crash between "model asked for a tool" and "tool finished"); the harness
  resolves or seals them so the model is never handed a dangling call.

`RunController` (`core/controller.py`) keeps the foreground UI thin: it parses
`/resume` variants into a `ResumePlan` and decides, via `attach`, what the UI
should do on attaching to a stored Run (`prompt_continue`, `approval_pending`,
`advance`, or `attached`) — none of which belongs in the Textual layer.

---

## 6. Seams

Everything outside the loop is a **seam** — a `Protocol` with a default adapter,
or a small named class — so an alternative can be swapped without touching the
harness. The defaults are wired in `make_agent_harness`.

| Seam | Protocol / type | Default adapter | Swap story |
|------|-----------------|-----------------|------------|
| **Model** | `Model` (`models/base.py`) | `OpenAIModel`, wrapped in `RetryingModel` | Any OpenAI-compatible or bespoke provider. |
| **Tools** | `Tool` + `ToolRegistry` (`harness/tools/`) | `default_tools()` — `read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash` | Register more tools; inputs are validated through Pydantic models. |
| **Tool policy** | `ToolPolicy` | `SessionToolPolicy` | Inline authorization / approval gating before execution. |
| **Checkpoint store** | `CheckpointStore` (`checkpoint/`) | `SqliteCheckpointStore` | Alternative durable backends. |
| **Sandbox** | `Sandbox` (`core/sandbox.py`) | `LocalSandbox`, injected via `sandbox_factory` | `DockerSandbox` (`adapters/docker/`) swaps this one seam for container execution; selected by `[sandbox].kind` in `.milky-frog/config.toml`. |
| **Context injection** | `ContextLoader` (`harness/prompt_context.py`) | `make_context_loader(home)` | Inject a system-prompt section at Run start. |
| **Token budget** | `TokenBudget` (`harness/budget.py`) | injected `TokenCounter` | `trim`s each `ModelRequest` before the model call — the one live example of per-call request shaping. |
| **Token counter** | `TokenCounter` (`tokens/base.py`) | `ApproxCharCounter` | `make_token_counter(provider)` picks `TiktokenCounter` (tiktoken) or `HFTokenizerCounter` (tokenizers) per Provider, degrading to approximate. Optional deps; core never imports them. |
| **UI driver** | `RunAdvancer` / `RunCanceller` (`ui/protocols.py`) | Textual app | Drive the interactive loop differently. |

> **Why protocols over inheritance.** Each seam has exactly one default and a
> clear reason an alternative might exist (a different model provider, real
> sandboxing, a different store). A `Protocol` keeps the harness depending on
> the *shape* of a collaborator, not its construction, so the swap is a one-line
> change in `assemble.py` and every test can inject a stub.

---

## 7. The Sandbox policy

The `Sandbox` seam (`core/sandbox.py`; default `LocalSandbox` in
`adapters/local/`) is a **policy boundary, not host isolation**. It:

- denies reads/writes to sensitive paths (e.g. `.env`, credential files),
  surfacing them as approval-required rather than silently failing;
- gates shell commands behind user approval;
- owns the subprocess environment for `bash`.

It does **not** contain untrusted code. Under `LocalSandbox` a determined
process still runs on the host. The boundary exists to stop a cooperating model
from *accidentally* touching something sensitive, and to put a human in the loop
for shell.

Both adapters route **every** shell command through `Sandbox.run_command()` —
`bash` (`harness/tools/builtins/bash.py`) and the post-edit
`VerificationHandler`. Nothing else in the codebase spawns a command. (MCP
servers are the one live exception: `McpClientManager` spawns its own stdio
subprocesses, because a long-lived piped process needs a `spawn()`-shaped seam
this protocol does not yet have. Tracked separately.)

### The Container Sandbox

`DockerSandbox` (`adapters/docker/`) is the opt-in alternative, enabled by:

```toml
[sandbox]
kind = "docker"
image = "python:3.12-bookworm"
workspace_mount = "/mnt/workspace"   # must live under /mnt
```

- The Workspace is **bind-mounted** at `workspace_mount`. `resolve()` therefore
  still returns a host path and delegates to a composed `LocalSandbox` — the
  deny-pattern policy is identical, and `read_file` / `write_file` / `edit_file`
  / `grep` / `list_dir` need no container awareness at all.
- `run_command()` is the only container-specific method: a container is created
  lazily per Workspace (`docker run -d … sleep infinity`) and reused for every
  subsequent `docker exec`. `DockerSandboxFactory.aclose()`, wired into
  `ShutdownManager`, removes them.
- `build_env()` does **not** forward host `HOME`/`PATH`/`SHELL` — those name host
  filesystem locations. `env_allowlist_extra` values do travel.
- Command execution is genuinely isolated; **file access is not**. A process in
  the container reaches the whole bind-mounted Workspace — including a writable
  `.git/`, so a hook written inside the container is executed *by the host* on
  the next git operation. This remains a policy boundary, not a defence against
  a hostile model. Isolating the Workspace itself is tracked in #83.
- A timeout kills the host-side `docker exec` client. The in-container process
  may linger until the container is removed at session end.

---

## 8. Skills, Memory, and context injection

- **Skills** (`harness/skills/`) are declarative `SKILL.md` bundles loaded by a
  `SkillCatalog`. They add task-specific operating *knowledge* to the agent and
  are **never executable** — a Skill is text the model reads, not code that
  runs. This keeps the trust surface small: loading a Skill cannot, by
  construction, execute anything.
- **Memory** is user-approved, project-scoped knowledge that survives across
  Runs (distinct from the per-Run transcript).
- **Context injection** is the supported way to add to the system prompt at Run
  start: the `ContextLoader` protocol is injected directly into `AgentHarness`
  and wired via `make_context_loader(settings.home)`. Note that this is *not*
  done through a lifecycle signal — `RunBeforeStart` is pure observation
  precisely so that "what goes into the prompt" has one explicit owner.

---

## 9. Trade-offs and non-goals

These are deliberate. Each removes a class of complexity the project decided it
does not need yet.

- **No concurrency / no background work.** One foreground Run at a time. Buys
  simple state reasoning and trivial recoverability; costs throughput. A second
  task waits.
- **No host isolation.** The sandbox is a policy, not a container. Buys a
  zero-dependency local tool; costs the ability to run untrusted code safely.
- **No event-sourced history.** Resume folds a single `RunState` snapshot, not a
  replayed log of events. Buys a simple, inspectable durable record; costs
  fine-grained time-travel within a Run.
- **No handler-driven control flow (yet).** Handlers observe; they do not steer.
  Authorization is inline. Buys an obvious, debuggable control path; the
  `HandlerResult` seam is reserved for when that trade-off needs to change.
- **OpenAI-compatible first.** The `Model` seam targets the OpenAI wire format.
  Other providers are a seam swap, not a core concern.
