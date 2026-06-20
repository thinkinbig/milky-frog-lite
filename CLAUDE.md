# CLAUDE.md

Guidance for working in this repository. See `README.md` for user-facing docs,
`CONTEXT.md` for the canonical domain glossary, and `docs/adr/` for the
trade-offs behind the architecture.

## What this is

Milky Frog (奶蛙) is a lightweight local coding-agent CLI. It runs one
foreground task at a time, coordinating model and Tool calls through a linear
Harness and persisting an append-only Checkpoint so Runs can be resumed.

Status: OpenAI-compatible foreground Runs and an interactive loop work today.
Built-in Tools, multi-turn state, and Checkpoint replay are not yet wired
(`milky-frog resume` is a stub).

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
model → Tool → model loop bounded by `max_model_calls`, appending a Checkpoint
event at every step and dispatching lifecycle Handlers around each model and
Tool call.

`runtime.py` (`MilkyFrog`) assembles the concrete pieces from `Settings` and
owns the sync→async boundary (a reused event loop). `cli/app.py` is the Typer
command surface; `ui/` renders everything via Rich.

Everything else is a **seam** — a `Protocol` with a default adapter, so
alternatives can be swapped without touching the Harness:

- `models/` — `Model` protocol, `OpenAIModel` adapter.
- `tools/` — `Tool` protocol + `ToolRegistry` (no built-in Tools yet).
- `checkpoint/` — `CheckpointStore` protocol, append-only `SqliteCheckpointStore`.
- `handlers/` — typed lifecycle events + `HandlerRegistry` (authorization,
  persistence, observability cross-cuts).
- `memory/` — cross-Run project knowledge seam.
- `skills/` — `SkillCatalog`, declarative `SKILL.md` bundles (never executable).
- `sandbox/` — `LocalSandbox` policy (denies `.git`, `.env`, keys; path-escape
  guard). A policy boundary, **not** host isolation.

`domain.py` holds the shared frozen dataclasses / enums (`RunStatus`, `Message`,
`ToolCall`, `RunRequest`, `RunResult`, …) — the vocabulary every layer uses.

## Conventions

- **Domain language is enforced.** Use the exact terms in `CONTEXT.md` (Run,
  Harness, Workspace, Tool, Handler, Checkpoint, Memory, Skill, Local Sandbox)
  and avoid the listed synonyms (session, workflow, plugin, middleware, …) in
  code, names, and docs.
- Python 3.12+, `from __future__ import annotations` at the top of modules.
- mypy is **strict** and ruff line length is 100; selected rules: E, F, I, UP,
  B, SIM, RUF.
- Prefer frozen `@dataclass(frozen=True, slots=True)` for value types; seams are
  `typing.Protocol`s.
- Tool inputs are validated through pydantic `BaseModel`s.
- Checkpoint events are append-only; never mutate prior events.
- Keep ADR decisions in mind before changing a seam; add a new ADR for
  significant architectural shifts.
