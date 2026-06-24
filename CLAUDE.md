# CLAUDE.md

Guidance for working in this repository. See `README.md` for user-facing docs,
`CONTEXT.md` for the canonical domain glossary, and `docs/adr/` for the
trade-offs behind the architecture.

## What this is

Milky Frog (奶蛙) is a lightweight local coding-agent CLI. It runs one
foreground task at a time, coordinating model and Tool calls through a linear
Harness and persisting a RunState Checkpoint snapshot so Runs can be resumed.

Status: OpenAI-compatible foreground Runs, built-in Tools, snapshot-based
resume (`milky-frog resume`), and a multi-turn interactive loop work today.
Resume loads a persisted `RunState` and repairs an interrupted Tool
(ADR-0009, ADR-0014); `resume(run_id, prompt)` continues a Run with a new
user turn so the interactive loop keeps one growing transcript across prompts
(ADR-0010).

## Commands

```bash
uv sync                         # install deps into .venv
uv run milky-frog               # interactive task loop
uv run milky-frog doctor        # verify config without a model request

uv run pytest                   # tests (asyncio_mode=auto, no marker needed)
uv run pytest tests/test_harness.py::test_name   # single test
uv run ruff check .             # lint
uv run ruff format --check .    # format check
uv run mypy                     # type-check (strict)
```

Run all four checks before considering work done; CI mirrors them.

## Model configuration

Read from environment variables (or a `.env` file in the cwd; real env vars win,
see `settings.py`):

- `MILKY_FROG_API_KEY` (required)
- `MILKY_FROG_MODEL` (required)
- `MILKY_FROG_BASE_URL` (optional, for OpenAI-compatible providers)
- `MILKY_FROG_HOME` (optional, state dir; default `~/.milky-frog`)

Per-workspace config lives in `.milky-frog/config.toml` (e.g. `max_model_calls`)
and is safe to commit. Credentials must never be committed.

## Architecture

The agent loop lives in `harness/agent_loop.py` (`AgentLoop.advance`): a linear
model → Tool → model loop bounded by `max_model_calls`, persisting a Checkpoint
snapshot after meaningful steps and notifying lifecycle Handlers around each model and
Tool call. `harness/agent_harness.py` (`AgentHarness`) wraps the loop with run
start, resume, and approval handling.

`agent_session.py` (`AgentSession`) assembles the concrete pieces from `Settings`
and owns the sync→async boundary (a reused event loop). `cli/app.py` is the Typer
command surface and drives the foreground interactive loop. `ui/` renders
everything via Rich.

### Three event lanes (do not unify)

Milky Frog uses three separate concepts. Never call them all “Event” without a
qualifier, and never merge them into one base type:

| Lane | Where | Lifetime | Purpose |
|------|-------|----------|---------|
| **Checkpoint snapshot** | `checkpoint/snapshot.py`, `runs.state_json` | Durable (SQLite) | Resume source of truth |
| **Lifecycle signal** | `handlers/events.py`, hub in `handlers/hub.py` (`EventHub`) | Ephemeral (in-process) | UI streaming, Langfuse (`broadcast`) |
| **Handler control return** | `HandlerResult` in `handlers/context.py` | Per-step | Authorization, context build, token budget |

RunState snapshots are serialized via Pydantic models in `checkpoint/snapshot.py` (ADR-0014).
Lifecycle signals are frozen dataclass subclasses in `handlers/events.py`.
Only `RunEmitter` publishes lifecycle signals; Handlers never publish. They
subscribe via `observe` / `on` / `subscribe`; most return `None` (pure
observation). Policy and context build are **not** a separate Harness mechanism —
they are expressed as `HandlerResult` control returns that the emitter applies to
the next step:

- `RunBeforeStart` → `SystemPromptSection` (additive context injection; e.g. `AgentContextHandler`).
- `RunBeforeModel` → carries the full `ModelRequest` and collects `HandlerResult`; this is the seam for per-call request shaping such as token budgeting (a reductive rewrite — needs its own result variant).
- `RunBeforeTool` → `BlockResult` / `ApprovalResult` (authorization; `PolicyHandler`).

### Seams

Everything else is a **seam** — a `Protocol` with a default adapter, or a small
named class — so alternatives can be swapped without touching the Harness:

- `models/` — `Model` protocol, `OpenAIModel` adapter.
- `harness/tools/` — `Tool` protocol + `ToolRegistry` + built-in Tools
  (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep`, `bash`).
- `checkpoint/` — `CheckpointStore` protocol, `SqliteCheckpointStore`, `RunSnapshot` serialization (ADR-0014).
- `harness/state.py` — transcript mutators and `repair_transcript` (interrupted-tool repair).
- `handlers/` — lifecycle signals + `EventHub` (ADR-0012); the Harness publishes.
- `ui/protocols.py` — `RunAdvancer`, `RunCanceller` for the interactive loop.
- `harness/skills/` — `SkillCatalog`, declarative `SKILL.md` bundles (never executable).
- `harness/sandbox/` — `Sandbox` protocol + `LocalSandbox`
  (path deny patterns, subprocess env, `sandbox_factory` injection). Implements the
  **Local Sandbox** policy from ADR-0003; a policy boundary, **not** host isolation.
  Future `DockerSandbox` swaps this single seam (ADR-0016).

`domain.py` holds the shared frozen dataclasses / enums (`RunStatus`, `Message`,
`ToolCall`, `RunRequest`, `RunResult`, …) — the vocabulary every layer uses.

### Runner emission matrix

The Harness alone decides what gets persisted vs notified (not every step does
both):

| Step | Checkpoint | Lifecycle `notify` |
|------|-----------|---------------------|
| Run start | `save_state` (seeded transcript) | `RunBeforeStart`, `RunStarted` |
| User message | `save_state` | — |
| In-run user message | — | `RunNotice` |
| After model | `save_state` | `RunAfterModel` |
| Streaming | — | `RunModelChunk`, `RunModelReasoning` |
| After tool | `save_state` | `RunAfterTool` |
| Run terminal | `save_state` + status | matching signal |

## Conventions

- **Domain language is enforced.** Use the exact terms in `CONTEXT.md` (Run,
  Harness, Workspace, Tool, Handler, Checkpoint, Memory, Skill, Local Sandbox)
  and avoid the listed synonyms (session, workflow, plugin, middleware, …) in
  code, names, and docs.
- Python 3.12+, `from __future__ import annotations` at the top of modules.
- mypy is **strict** and ruff line length is 100; selected rules: E, F, I, UP,
  B, SIM, RUF.
- Prefer frozen `@dataclass(frozen=True, slots=True)` for domain value types;
  Pydantic `BaseModel` for Checkpoint bodies and lifecycle signals; seams are
  `typing.Protocol`s or small named classes — **no bare `lambda`** for callbacks
  or sort keys in production code.
- Tool inputs are validated through pydantic `BaseModel`s.
- Checkpoint events are append-only; never mutate prior events.
- Tests may use named stub classes in `tests/stubs.py` instead of lambdas.
- Keep ADR decisions in mind before changing a seam; add a new ADR for
  significant architectural shifts.

## Key ADRs

- [ADR-0012](docs/adr/0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md) — Handler bus is notify-only.
- [ADR-0014](docs/adr/0014-persist-checkpoints-as-runstate-snapshots.md) — RunState snapshot persistence.
- [ADR-0016](docs/adr/0016-unify-command-env-into-sandbox.md) — `CommandEnvironment` merged into `Sandbox` seam.
