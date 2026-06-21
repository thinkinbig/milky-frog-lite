"""Help the human curate mined change tasks before they become a benchmark.

Mining is automatic; the dataset is not trustworthy until a person reads each
task. This prints, per task, the prompt + ground-truth files + the real gold
diff, and flags likely problems (leaky prompt, empty read-target, deeply nested
change that needs legitimate parent-package reads). Edit change_tasks.json by
hand afterwards: refine ``prompt``, add ``also_in_scope`` paths, set
``reviewed: true``, or delete a task.

Usage:
    uv run python -m evals.review_tasks            # all tasks, flags only
    uv run python -m evals.review_tasks --diff     # include the gold diff
    uv run python -m evals.review_tasks --task <id>
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from evals.scoring import relevant_dirs

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "evals" / "datasets" / "change_tasks.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def flags(task: dict[str, Any]) -> list[str]:
    out: list[str] = []
    prompt = task["prompt"]
    if len(prompt) < 25:
        out.append("SHORT_PROMPT")
    if not task["relevant_files"]:
        out.append("NO_READ_TARGET (added-only)")
    # leakage: prompt mentions a changed file's stem
    stems = {Path(c["path"]).stem for c in task["changed_files"]}
    if any(s in prompt for s in stems if len(s) > 3):
        out.append("LEAKY_PROMPT (names a file)")
    # deeply nested change -> parent-package reads will look like noise
    if any(p.count("/") >= 4 for p in task["relevant_files"]):
        out.append("DEEP_NESTING (consider also_in_scope)")
    if task.get("reviewed"):
        out.append("reviewed✓")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", type=str, default=None)
    ap.add_argument("--diff", action="store_true", help="print the gold diff")
    args = ap.parse_args()

    tasks = json.loads(DATASET.read_text())
    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]

    unreviewed = 0
    for t in tasks:
        fl = flags(t)
        if not t.get("reviewed"):
            unreviewed += 1
        print("=" * 90)
        print(f"{t['task_id']}   {' '.join(fl)}")
        print(f"  prompt:   {t['prompt']}")
        print(f"  relevant: {t['relevant_files']}")
        if t["added_files"]:
            print(f"  added:    {t['added_files']}")
        changed_paths = [c["path"] for c in t["changed_files"]]
        also = t.get("also_in_scope", []) or []
        scoped_dirs = relevant_dirs(changed_paths) | set(also)
        exact_files = set(changed_paths) | set(also)
        root_exact = sorted(p for p in exact_files if "/" not in p)
        print(f"  scope dirs:  {sorted(scoped_dirs)}")
        if root_exact:
            print(f"  root files:  {root_exact}")
        if args.diff:
            print(_git("show", "--stat", "--format=%b", t["gold_sha"]))

    print("=" * 90)
    print(f"{len(tasks)} tasks, {unreviewed} not yet reviewed")
    print("Edit evals/datasets/change_tasks.json: refine prompt, add `also_in_scope`,")
    print('set `"reviewed": true`, or delete. Flags above mark what to look at.')


if __name__ == "__main__":
    main()
