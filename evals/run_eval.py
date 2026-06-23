"""Run the read-noise eval: drive the live agent over mined change tasks.

For each task the harness checks out the commit's *parent* into a throwaway git
worktree, runs the agent there with the task prompt, collects the files it reads
(``ReadCollector``), and scores them against the changed package (method 3).

Usage (needs MILKY_FROG_API_KEY / MODEL in env or .env at repo root):
    uv run python -m evals.run_eval --limit 1 --max-model-calls 8   # smoke test
    uv run python -m evals.run_eval --repeats 3                      # full set, 3x
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from milky_frog.runtime import MilkyFrog

from evals.read_collector import ReadCollector, ReadRecord
from evals.scoring import TaskScore, score_run
from milky_frog.handlers import EventDispatcher
from milky_frog.harness.tools import PermissivePolicy
from milky_frog.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "evals" / "datasets" / "change_tasks.json"
RESULTS = REPO_ROOT / "evals" / "results"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _normalize(path: str, workspace: Path) -> str:
    """Make an agent-supplied read path workspace-relative + posix for scoring."""
    p = path.strip()
    try:
        if os.path.isabs(p):
            p = os.path.relpath(p, workspace)
    except ValueError:
        return p
    return p.replace(os.sep, "/").removeprefix("./")


def run_task(settings: Settings, task: dict[str, Any], max_model_calls: int) -> TaskScore:
    workspace = Path(tempfile.mkdtemp(prefix="mf-eval-"))
    _git("worktree", "add", "--detach", str(workspace), task["base_ref"])
    try:
        cfg = workspace / ".milky-frog"
        cfg.mkdir(exist_ok=True)
        (cfg / "config.toml").write_text(f"max_model_calls = {max_model_calls}\n")

        bus = EventDispatcher()
        collector = ReadCollector()
        collector.register(bus)
        # Eval runs unattended: auto-approve every tool so the agent's natural
        # reading behaviour (the thing we measure) isn't gated behind prompts.
        with MilkyFrog.from_settings(
            settings, handlers=bus, bundles=[collector], tool_policy=PermissivePolicy()
        ) as frog:
            result = frog.run(task["prompt"], workspace)

        reads = [
            ReadRecord(_normalize(r.path, workspace), r.is_error)
            for r in collector.reads.get(result.run_id, [])
        ]
        edits = collector.edits.get(result.run_id, [])
        return score_run(
            task_id=task["task_id"],
            reads=reads,
            edits=[_normalize(e, workspace) for e in edits],
            changed_files=[c["path"] for c in task["changed_files"]],
            relevant_files=task["relevant_files"],
            also_in_scope=task.get("also_in_scope"),
        )
    finally:
        _git("worktree", "remove", "--force", str(workspace))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only the first N tasks")
    ap.add_argument("--task", type=str, default=None, help="run a single task_id")
    ap.add_argument("--repeats", type=int, default=1, help="runs per task (stochastic)")
    ap.add_argument("--max-model-calls", type=int, default=12, help="cap per Run")
    args = ap.parse_args()

    settings = Settings.from_environment()
    tasks = json.loads(DATASET.read_text())
    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
    if args.limit:
        tasks = tasks[: args.limit]

    scores: list[TaskScore] = []
    for task in tasks:
        for rep in range(args.repeats):
            print(f"▶ {task['task_id']} (rep {rep + 1}/{args.repeats}) …", flush=True)
            score = run_task(settings, task, args.max_model_calls)
            scores.append(score)
            print(
                f"    precision={score.scope_precision:.2f} noise={score.noise_rate:.2f} "
                f"reads={score.reads_total} in/out={score.in_scope}/{score.out_of_scope} "
                f"failed={score.failed_reads} dup={score.duplicate_reads} "
                f"edits={score.edits_total} "
                f"reads/edit={score.reads_per_edit if score.reads_per_edit is not None else 'N/A'} "
                f"{'✓' if score.completed else '✗ (no edits)'}"
            )
            if score.out_of_scope_paths:
                print(f"    noise paths: {score.out_of_scope_paths}")

    if not scores:
        print("no tasks matched")
        return

    precisions = [s.scope_precision for s in scores]
    completed = [s for s in scores if s.completed]
    print("\n── aggregate (all) ──")
    print(
        f"runs={len(scores)}  completed={len(completed)}  "
        f"median_precision={statistics.median(precisions):.2f}  "
        f"mean_precision={statistics.mean(precisions):.2f}  "
        f"median_reads={statistics.median(s.reads_total for s in scores):.0f}"
    )
    if completed:
        cprec = [s.scope_precision for s in completed]
        med_rpe = statistics.median(
            s.reads_per_edit for s in completed if s.reads_per_edit is not None
        )
        print("\n── aggregate (completed only) ──")
        print(
            f"runs={len(completed)}  "
            f"median_precision={statistics.median(cprec):.2f}  "
            f"mean_precision={statistics.mean(cprec):.2f}  "
            f"median_reads={statistics.median(s.reads_total for s in completed):.0f}  "
            f"median_reads_per_edit={med_rpe:.1f}"
        )

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "latest.json"
    out.write_text(json.dumps([asdict(s) for s in scores], ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
