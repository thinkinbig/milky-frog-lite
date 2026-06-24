# AGENTS.md

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

The agent loop lives in `harness/runner.py` (`Harness.run`): a linear
model → Tool → model loop bounded by `max_model_calls`, persisting a Checkpoint
snapshot after meaningful steps and notifying lifecycle Handlers around each model and
Tool call.

`agent_session.py` (`AgentSession`) assembles the concrete pieces from `Settings`
and owns async resource lifetime plus Run orchestration. `cli/app.py` is the Typer
command surface; `ui/tui/app.py` wires the interactive loop through Textual.
`ui/` renders one-shot command output via Rich and interactive Runs via Textual.

### Three event lanes (do not unify)

Milky Frog uses three separate concepts. Never call them all “Event” without a
qualifier, and never merge them into one base type:

| Lane | Where | Lifetime | Purpose |
|------|-------|----------|---------|
| **Checkpoint snapshot** | `checkpoint/snapshot.py`, `runs.state_json` | Durable (SQLite) | Resume source of truth |
| **Lifecycle signal** | `handlers/events.py`, bus in `handlers/dispatcher.py` | Ephemeral (in-process) | UI streaming, Langfuse (`notify`) |
| **Harness policy** | Explicit `Protocol` deps on `Harness` (future) | Per-call | Authorization, context build, etc. |

RunState snapshots are serialized via Pydantic models in `checkpoint/snapshot.py` (ADR-0014).
Lifecycle signals are frozen dataclass subclasses in `handlers/events.py`.
Only `RunEmitter` publishes lifecycle signals. Handlers subscribe via
`observe` / `on` / `subscribe`; most return `None`. `RunBeforeTool` and
`RunBeforeStart` may return `HandlerResult` values that influence the next
Harness step. Handlers do not publish signals themselves.

### Seams

Everything else is a **seam** — a `Protocol` with a default adapter, or a small
named class — so alternatives can be swapped without touching the Harness:

- `models/` — `Model` protocol, `OpenAIModel` adapter.
- `harness/tools/` — `Tool` protocol + `ToolRegistry` + built-in Tools.
- `checkpoint/` — `CheckpointStore` protocol, `SqliteCheckpointStore`, `RunSnapshot` serialization (ADR-0014).
- `harness/state.py` — transcript mutators and `repair_transcript` (interrupted-tool repair).
- `handlers/` — lifecycle signals + `EventDispatcher` (ADR-0012); only `RunEmitter` publishes.
- `harness/skills/` — `SkillCatalog`, declarative `SKILL.md` bundles (never executable).
- `infra/sandbox/` — `LocalSandbox` policy (denies `.git`, `.env`, keys; path-escape
  guard). A policy boundary, **not** host isolation.

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
  `typing.Protocol`s or small named classes. Avoid `lambda` for non-trivial
  callbacks and ad-hoc seams; simple one-liner sort keys (e.g.
  `key=lambda p: (not p.is_dir(), p.name)`) are fine.
- Tool inputs are validated through pydantic `BaseModel`s.
- Checkpoint snapshots are replaced at durable boundaries; never reintroduce an append-only Checkpoint event log without a new ADR.
- Tests may use named stub classes in `tests/stubs.py` for reusable doubles;
  simple one-off lambdas are fine elsewhere.
- Keep ADR decisions in mind before changing a seam; add a new ADR for
  significant architectural shifts.

## Key ADRs

- [ADR-0012](docs/adr/0012-shrink-handler-registry-to-a-read-only-lifecycle-bus.md) — Handler bus is notify-only.
- [ADR-0014](docs/adr/0014-persist-checkpoints-as-runstate-snapshots.md) — RunState snapshot persistence.
