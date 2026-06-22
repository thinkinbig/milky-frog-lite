"""Mine the repo's git history into change-task candidates for read-noise evals.

Each selected commit becomes a task: run the agent at the commit's *parent*
(so the change has not happened yet), ask it to make the change, and compare the
files it reads against the files the commit actually touched (ground truth).

Selection favours small, focused feat/fix/refactor commits where the
"relevant files" set is tight enough for read-precision to be meaningful.
Large refactors, rename churn, and docs/config-only commits are skipped.

Usage:
    uv run python evals/mine_change_tasks.py            # write dataset + print table
    uv run python evals/mine_change_tasks.py --max 20   # cap candidates
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "evals" / "datasets" / "change_tasks.json"

# A commit qualifies only if its subject starts with one of these types.
ALLOWED_TYPES = ("feat", "fix", "refactor", "perf")
# Tight bound on modified+added source files keeps the relevant set meaningful.
MIN_SRC_FILES = 1
MAX_SRC_FILES = 6
# Commits touching more files than this overall are treated as sweeping refactors.
MAX_TOTAL_FILES = 12
SRC_PY = re.compile(r"^src/.*\.py$")


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str  # M, A, D, or R (rename)


@dataclass
class ChangeTask:
    task_id: str
    gold_sha: str
    base_ref: str  # parent commit; agent starts here
    subject: str
    body: str
    prompt: str  # editable; defaults to the subject's description
    relevant_files: list[str]  # modified source files — the read targets
    added_files: list[str]  # new files; created, not read
    changed_files: list[dict[str, str]]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _name_status(sha: str) -> list[ChangedFile]:
    out = _git("show", "--no-renames", "--name-status", "--format=", sha)
    files: list[ChangedFile] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        files.append(ChangedFile(path=path, status=status))
    return files


def _slug(subject: str) -> str:
    s = re.sub(r"^(feat|fix|refactor|perf|chore|docs|test|tests)(\([^)]*\))?:\s*", "", subject)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:48] or "task"


def _prompt_from(subject: str) -> str:
    """Turn a commit subject into a task instruction (strip the conventional prefix)."""
    return re.sub(r"^(feat|fix|refactor|perf)(\([^)]*\))?:\s*", "", subject).strip()


def mine(max_candidates: int) -> list[ChangeTask]:
    shas = _git("rev-list", "--no-merges", "HEAD").split()
    tasks: list[ChangeTask] = []
    for sha in shas:
        subject = _git("show", "-s", "--format=%s", sha).strip()
        type_ok = subject.split(":", 1)[0].split("(", 1)[0] in ALLOWED_TYPES
        if not type_ok:
            continue
        changed = _name_status(sha)
        if not changed or len(changed) > MAX_TOTAL_FILES:
            continue
        if any(c.status == "R" for c in changed):  # rename churn — fuzzy ground truth
            continue
        src = [c for c in changed if SRC_PY.match(c.path)]
        src_touch = [c for c in src if c.status in ("M", "A")]
        if not (MIN_SRC_FILES <= len(src_touch) <= MAX_SRC_FILES):
            continue
        body = _git("show", "-s", "--format=%b", sha).strip()
        relevant = [c.path for c in src if c.status == "M"]
        added = [c.path for c in changed if c.status == "A"]
        if not relevant and not added:
            continue
        tasks.append(
            ChangeTask(
                task_id=_slug(subject),
                gold_sha=sha,
                base_ref=f"{sha}^",
                subject=subject,
                body=body,
                prompt=_prompt_from(subject),
                relevant_files=relevant,
                added_files=added,
                changed_files=[asdict(c) for c in changed],
            )
        )
        if len(tasks) >= max_candidates:
            break
    return tasks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=20, help="max candidate tasks")
    args = ap.parse_args()

    tasks = mine(args.max)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2) + "\n")

    print(f"wrote {len(tasks)} change tasks -> {OUT_PATH.relative_to(REPO_ROOT)}\n")
    print(f"{'task_id':40} {'relevant':>8} {'added':>5}  prompt")
    print("-" * 100)
    for t in tasks:
        print(f"{t.task_id:40} {len(t.relevant_files):>8} {len(t.added_files):>5}  {t.prompt[:50]}")


if __name__ == "__main__":
    main()
