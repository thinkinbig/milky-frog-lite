"""Run the read-noise pilot: drive milky-frog over SWE-bench change tasks.

For each task this clones the repo at ``base_commit``, runs the Agent on the
``problem_statement`` verbatim, and records — via the read-only ``EventHub``
``RunAfterTool`` seam — every file the Run reads and edits. It computes **no
scores**: it emits the raw ``RunsArtifact`` that ``score`` turns into footprint
ratio / waste metrics / the recall gate.

Design commitments visible here (see the pilot design doc):

- **Generous cap + partition by termination** — ``--max-model-calls`` is set high
  so it rarely binds; each Run is tagged ``completed`` / ``capped`` / ``failed``
  so the scorer keeps only naturally-finished Runs and quarantines the rest.
- **Pinned tool surface** — Langfuse off, ``home`` pinned to a clean dir, and cwd
  set to the workspace so no repo-level MCP server / Skill / Memory leaks in.
- **No prompt contamination** — the ``problem_statement`` is passed as-is.

Requires MILKY_FROG_API_KEY + MILKY_FROG_MODEL, network (to clone repos), and
``git``. Real model calls cost money; start with ``--limit``.

    uv run python -m evals.read_noise.run --limit 2 --repeats 1
    uv run python -m evals.read_noise.run --repeats 3 --max-model-calls 30
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from evals._settings import with_pinned_home, without_observability
from evals.read_collector import ReadCollector
from evals.read_noise.schema import (
    FileEvent,
    ReadNoiseTask,
    ReadRef,
    RunRecord,
    RunsArtifact,
    TaskRuns,
    dump_runs,
    load_tasks,
    normalize_read_path,
    termination_kind,
)
from evals.tool_collector import ToolCallCollector
from milky_frog.app.session import AgentSession
from milky_frog.events import EventHub
from milky_frog.project import PROJECT_DIRNAME
from milky_frog.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "evals" / "datasets" / "read_noise_tasks.json"
CACHE_DIR = REPO_ROOT / "evals" / ".cache" / "repos"
# A dedicated empty home so no user MCP server / Skill / Memory leaks into the
# baseline Harness (they all load from `home`). Persisted so the tokenizer cache
# survives across sweeps; it never receives mcp.json/skills/memory.
EVAL_HOME = REPO_ROOT / "evals" / ".cache" / "eval-home"
RESULTS = REPO_ROOT / "evals" / "results"


# --- workspace materialization ---------------------------------------------


def _run_git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def ensure_repo_cache(repo: str) -> Path:
    """Clone ``owner/name`` into the local cache once; reuse it thereafter."""
    cache = CACHE_DIR / repo.replace("/", "__")
    if not (cache / ".git").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _run_git("clone", "--quiet", f"https://github.com/{repo}.git", str(cache))
    return cache


def checkout_workspace(repo: str, base_commit: str, max_model_calls: int) -> Path:
    """A fresh workspace = a local clone of the cache, detached at base_commit."""
    cache = ensure_repo_cache(repo)
    workspace = Path(tempfile.mkdtemp(prefix="mf-read-noise-"))
    _run_git("clone", "--quiet", "--local", str(cache), str(workspace))
    _run_git("-C", str(workspace), "checkout", "--quiet", "--detach", base_commit)
    cfg = workspace / PROJECT_DIRNAME
    cfg.mkdir(exist_ok=True)
    (cfg / "config.toml").write_text(f"max_model_calls = {max_model_calls}\n")
    return workspace


# --- running ----------------------------------------------------------------


async def _run_once(
    settings: Settings, task: ReadNoiseTask, run_index: int, max_model_calls: int
) -> RunRecord:
    workspace = checkout_workspace(task.repo, task.base_commit, max_model_calls)
    origin = Path.cwd()
    try:
        # Session setup reads ``Path.cwd()`` for project + MCP config (session.py).
        # Point cwd at the clean workspace so the milky-frog repo's own
        # `.milky-frog/mcp.json` (a github MCP server) can't leak into the Run —
        # this also mirrors how a user actually runs milky-frog: from the project.
        os.chdir(workspace)
        bus = EventHub()
        reads = ReadCollector()
        tools = ToolCallCollector()
        async with AgentSession.from_settings(settings, hub=bus, bundles=[reads, tools]) as session:
            session.policy.auto_approve()
            result = await session.start_new(task.problem_statement, workspace)

        run_id = result.run_id
        events = [
            FileEvent(t.kind, normalize_read_path(t.path, workspace), t.is_error)
            for t in reads.touches.get(run_id, [])
        ]
        return RunRecord(
            run_index=run_index,
            status=result.status.value,
            termination=termination_kind(result.status),
            model_calls=result.model_calls,
            # Per-kind projections of ``events``, kept for the order-free
            # metrics (footprint, failed reads, the gold-edit gate).
            reads=[ReadRef(e.path, e.is_error) for e in events if e.kind == "read"],
            edits=[e.path for e in events if e.kind == "edit"],
            tool_calls=len(tools.calls.get(run_id, [])),
            final_message=result.final_message,
            events=events,
        )
    finally:
        os.chdir(origin)  # restore before rmtree — can't remove the cwd
        shutil.rmtree(workspace, ignore_errors=True)


def run_task(
    settings: Settings, task: ReadNoiseTask, repeats: int, max_model_calls: int
) -> TaskRuns:
    result = TaskRuns(
        task_id=task.task_id,
        repo=task.repo,
        base_commit=task.base_commit,
        relevant_files=task.relevant_files,
        added_files=task.added_files,
        runs=[],
    )
    for run_index in range(repeats):
        try:
            record = asyncio.run(_run_once(settings, task, run_index, max_model_calls))
        except Exception as exc:  # one bad Run must not sink the whole sweep
            record = RunRecord(
                run_index=run_index,
                status="error",
                termination="failed",
                model_calls=0,
                reads=[],
                edits=[],
                tool_calls=0,
                final_message=f"{type(exc).__name__}: {exc}",
            )
        result.runs.append(record)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--limit", type=int, default=None, help="only the first N tasks")
    ap.add_argument("--task", type=str, default=None, help="comma-separated task_id(s)")
    ap.add_argument("--repeats", type=int, default=3, help="Runs per task (N)")
    ap.add_argument("--max-model-calls", type=int, default=30, help="generous cap")
    ap.add_argument("--out", type=Path, default=RESULTS / "read_noise_runs.json")
    args = ap.parse_args()

    tasks = load_tasks(args.dataset)
    if args.task:
        wanted = {name.strip() for name in args.task.split(",")}
        tasks = [t for t in tasks if t.task_id in wanted]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks matched")
        return

    EVAL_HOME.mkdir(parents=True, exist_ok=True)
    settings = with_pinned_home(without_observability(Settings.from_environment()), EVAL_HOME)
    results: list[TaskRuns] = []
    for task in tasks:
        print(f"▶ {task.task_id} ({task.repo}) x{args.repeats} …", flush=True)
        started = time.perf_counter()
        result = run_task(settings, task, args.repeats, args.max_model_calls)
        results.append(result)
        for run in result.runs:
            reads_ok = sum(1 for r in run.reads if not r.is_error)
            print(
                f"    run {run.run_index}: {run.termination:9} "
                f"{run.model_calls:>2} calls, {reads_ok} reads, {len(run.edits)} edits"
            )
        print(f"    ({time.perf_counter() - started:.1f}s)")

    artifact = RunsArtifact(
        meta={
            "model": settings.model,
            "max_model_calls": args.max_model_calls,
            "repeats": args.repeats,
            "dataset": args.dataset.name,
            "tasks": len(results),
        },
        tasks=results,
    )
    dump_runs(artifact, args.out)
    print(f"\nwrote {_display_path(args.out)}")


def _display_path(path: Path) -> str:
    """Shorten to a repo-relative path when possible.

    ``relative_to`` raises for any path outside the repo *and* for relative
    paths, so an ``--out`` the caller passed as anything but an absolute path
    inside the repo used to crash the process after a completed sweep — losing
    the exit code of an hour-long run over a cosmetic line.
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
