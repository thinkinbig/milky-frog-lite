"""Sample a stratified-and-filtered read-noise dataset from SWE-bench Verified.

See ``docs/evals/read-noise-pilot-design.md`` for the rationale behind every
knob. In short: SWE-bench Verified gold patches give a human-curated "files that
legitimately needed touching" ground truth on large real repos (the noise
surface a footprint metric needs); we keep only tight patches (1-6 non-test
source files) and round-robin across repos so one big repo can't dominate.
Selection is fully determined by ``--seed``. We consume only
``problem_statement`` + ``base_commit`` + the gold ``patch``; tests never run.

``datasets`` is not a project dependency — pull it in ephemerally:
    uv run --with datasets python -m evals.read_noise.sample
    uv run --with datasets python -m evals.read_noise.sample --size 40 --seed 13
"""

from __future__ import annotations

import argparse
import random
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.read_noise.schema import ReadNoiseTask, dump_tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "evals" / "datasets" / "read_noise_tasks.json"
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"

# A file is "test" (not a read target) if it lives under a tests dir or matches
# the pytest/unittest naming conventions used across the Verified repos.
TEST_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    status: str  # M, A, D, or R (rename)


def _is_source(path: str) -> bool:
    return path.endswith(".py") and not TEST_RE.search(path)


def parse_patch(patch: str) -> list[ChangedFile]:
    """Extract per-file status from a unified git diff (the gold patch)."""
    files: list[ChangedFile] = []
    blocks = re.split(r"(?m)^(?=diff --git )", patch)
    for block in blocks:
        header = _DIFF_HEADER.match(block.splitlines()[0]) if block.strip() else None
        if header is None:
            continue
        a_path, b_path = header.group(1), header.group(2)
        if "\nrename from " in block or "\nrename to " in block:
            to_match = re.search(r"(?m)^rename to (.+)$", block)
            files.append(ChangedFile(path=(to_match.group(1) if to_match else b_path), status="R"))
        elif "\nnew file mode " in block:
            files.append(ChangedFile(path=b_path, status="A"))
        elif "\ndeleted file mode " in block:
            files.append(ChangedFile(path=a_path, status="D"))
        else:
            files.append(ChangedFile(path=b_path, status="M"))
    return files


def eligible_task(
    row: dict[str, Any], *, min_src: int, max_src: int, max_total: int
) -> ReadNoiseTask | None:
    """Return a task if the row passes the tight-patch filter, else None."""
    changed = parse_patch(row["patch"])
    if not changed or len(changed) > max_total:
        return None
    if any(c.status == "R" for c in changed):  # rename churn — fuzzy ground truth
        return None
    src_touch = [c for c in changed if c.status in ("M", "A") and _is_source(c.path)]
    if not (min_src <= len(src_touch) <= max_src):
        return None
    relevant = [c.path for c in changed if c.status == "M" and _is_source(c.path)]
    added = [c.path for c in changed if c.status == "A" and _is_source(c.path)]
    if not relevant and not added:  # source touched only via deletes — nothing to read
        return None
    return ReadNoiseTask(
        task_id=row["instance_id"],
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        relevant_files=relevant,
        added_files=added,
        changed_files=[asdict(c) for c in changed],
    )


def stratify(
    by_repo: dict[str, list[ReadNoiseTask]],
    *,
    size: int,
    per_repo_cap: int,
    rng: random.Random,
) -> list[ReadNoiseTask]:
    """Round-robin across repos (each shuffled) so no single repo dominates."""
    buckets = {repo: list(tasks) for repo, tasks in by_repo.items()}
    for tasks in buckets.values():
        rng.shuffle(tasks)
    taken: dict[str, int] = defaultdict(int)
    picked: list[ReadNoiseTask] = []
    repos = sorted(buckets)
    progressed = True
    while len(picked) < size and progressed:
        progressed = False
        for repo in repos:
            if len(picked) >= size:
                break
            if taken[repo] >= per_repo_cap or not buckets[repo]:
                continue
            picked.append(buckets[repo].pop())
            taken[repo] += 1
            progressed = True
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="HuggingFace dataset id")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--size", type=int, default=40, help="target task count")
    ap.add_argument("--per-repo-cap", type=int, default=6, help="max tasks from one repo")
    ap.add_argument("--min-repos", type=int, default=6, help="require at least this many repos")
    ap.add_argument("--seed", type=int, default=13, help="fixed selection seed")
    ap.add_argument("--min-src", type=int, default=1, help="min non-test source files")
    ap.add_argument("--max-src", type=int, default=6, help="max non-test source files")
    ap.add_argument("--max-total", type=int, default=12, help="max total files (sweep guard)")
    args = ap.parse_args()

    from datasets import load_dataset  # ephemeral dep; imported late so --help works without it

    rows = load_dataset(args.dataset, split="test")
    by_repo: dict[str, list[ReadNoiseTask]] = defaultdict(list)
    eligible = 0
    for row in rows:
        task = eligible_task(
            row, min_src=args.min_src, max_src=args.max_src, max_total=args.max_total
        )
        if task is not None:
            by_repo[task.repo].append(task)
            eligible += 1

    picked = stratify(
        by_repo, size=args.size, per_repo_cap=args.per_repo_cap, rng=random.Random(args.seed)
    )
    repos_used = sorted({t.repo for t in picked})
    if len(repos_used) < args.min_repos:
        raise SystemExit(
            f"only {len(repos_used)} repos in sample (need >= {args.min_repos}); "
            "loosen the filter or lower --per-repo-cap"
        )

    dump_tasks(picked, args.out)

    print(
        f"eligible={eligible} across {len(by_repo)} repos "
        f"-> sampled {len(picked)} across {len(repos_used)} repos (seed={args.seed})"
    )
    print(f"wrote {args.out.relative_to(REPO_ROOT)}\n")
    per_repo: dict[str, int] = defaultdict(int)
    for t in picked:
        per_repo[t.repo] += 1
    print(f"{'repo':40} {'tasks':>5}")
    print("-" * 48)
    for repo in sorted(per_repo):
        print(f"{repo:40} {per_repo[repo]:>5}")
    print("-" * 48)
    print(f"{'total':40} {len(picked):>5}\n")
    print(f"{'task_id':45} {'src':>3}  problem (head)")
    print("-" * 100)
    for t in picked:
        head = t.problem_statement.strip().replace("\n", " ")[:44]
        print(f"{t.task_id:45} {len(t.relevant_files) + len(t.added_files):>3}  {head}")


if __name__ == "__main__":
    main()
