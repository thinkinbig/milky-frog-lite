"""Read-noise scoring — method 3: noise = reads outside the changed package.

A read is *in scope* if its path is one of the task's changed files, or sits
under a directory that the change touched. Everything else is *noise* — this is
exactly the "read the whole ui/presenter subsystem to answer one question"
pattern seen in the Langfuse logs. No import graph needed; the changed files'
own directories define the legitimate neighbourhood.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from evals.read_collector import ReadRecord


@dataclass(frozen=True, slots=True)
class TaskScore:
    task_id: str
    reads_total: int  # all read_file calls (incl. duplicates and failures)
    reads_ok_unique: int  # distinct successfully-read files (precision denominator)
    in_scope: int  # of reads_ok_unique, how many are in the changed neighbourhood
    out_of_scope: int  # the rest — noise
    scope_precision: float  # in_scope / reads_ok_unique  (1.0 = no noise)
    noise_rate: float  # 1 - scope_precision
    failed_reads: int  # directory-as-file / sensitive-path / missing — wasted calls
    duplicate_reads: int  # same file read more than once in the Run
    edits_total: int
    reads_per_edit: float | None  # reads_total / edits_total; None when no edits
    completed: bool  # True when the agent produced at least one edit
    relevant_hit: int  # how many modified files the agent actually read (recall)
    relevant_total: int
    out_of_scope_paths: list[str] = field(default_factory=list)


def relevant_dirs(changed_files: list[str]) -> set[str]:
    """Directories the change touched — the in-scope neighbourhood."""
    return {d for d in (posixpath.dirname(p) for p in changed_files) if d}


def _in_scope(path: str, dirs: set[str], changed: set[str]) -> bool:
    # Exact-directory match, not subtree: a change in ``ui/`` does NOT make
    # ``ui/presenter/`` in scope — reading a whole sibling subpackage is the
    # noise pattern we want to catch.
    return path in changed or posixpath.dirname(path) in dirs


def score_run(
    task_id: str,
    reads: list[ReadRecord],
    edits: list[str],
    changed_files: list[str],
    relevant_files: list[str],
    also_in_scope: list[str] | None = None,
) -> TaskScore:
    # ``also_in_scope`` is the human-curated escape hatch: files/dirs a task
    # legitimately needs to read but doesn't change (e.g. a Tool protocol one
    # package up). Each entry counts both as an exact file and as a directory.
    extra = also_in_scope or []
    dirs = relevant_dirs(changed_files) | set(extra)
    changed = set(changed_files) | set(extra)

    ok = [r for r in reads if not r.is_error]
    ok_unique = list(dict.fromkeys(r.path for r in ok))  # stable de-dup
    in_scope = [p for p in ok_unique if _in_scope(p, dirs, changed)]
    out_of_scope = [p for p in ok_unique if not _in_scope(p, dirs, changed)]

    denom = len(ok_unique) or 1
    precision = len(in_scope) / denom
    relevant_read = sum(1 for p in relevant_files if p in set(ok_unique))

    return TaskScore(
        task_id=task_id,
        reads_total=len(reads),
        reads_ok_unique=len(ok_unique),
        in_scope=len(in_scope),
        out_of_scope=len(out_of_scope),
        scope_precision=round(precision, 3),
        noise_rate=round(1 - precision, 3),
        failed_reads=sum(1 for r in reads if r.is_error),
        duplicate_reads=len(ok) - len(ok_unique),
        edits_total=len(edits),
        reads_per_edit=round(len(reads) / len(edits), 2) if edits else None,
        completed=len(edits) > 0,
        relevant_hit=relevant_read,
        relevant_total=len(relevant_files),
        out_of_scope_paths=out_of_scope,
    )
