"""Score the read-noise pilot's raw run records into the pilot's metrics.

Consumes the ``RunsArtifact`` from ``run`` and computes, per
``docs/evals/read-noise-pilot-design.md``:

- **Headline — footprint ratio** = ``distinct_files_read / |gold_source_files|``,
  a task-intrinsic (un-gameable) denominator, comparable across tasks.
- **Progress gate** = the Run edited at least one gold source file. Footprint is
  computed **only over runs that completed naturally and pass the gate** — a Run
  that reads little and quits must not win.
- **Waste** — ``failed_reads`` (directory-as-file / sensitive-path probes: the
  *unambiguous* waste) as a headline; ``duplicate_reads`` as a soft diagnostic.
- **Partition by termination** — only ``completed`` Runs are eligible; ``capped``
  and ``failed`` are reported in their own buckets, never averaged in.

    uv run python -m evals.read_noise.score
    uv run python -m evals.read_noise.score --runs evals/results/read_noise_runs.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evals.read_noise.schema import RunRecord, TaskRuns, load_runs

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS = REPO_ROOT / "evals" / "results" / "read_noise_runs.json"
DEFAULT_OUT = REPO_ROOT / "evals" / "results" / "read_noise_scores.json"


@dataclass(frozen=True, slots=True)
class RunScore:
    run_index: int
    termination: str
    distinct_reads: int
    duplicate_reads: int  # soft diagnostic (truncation re-reads are legitimate)
    # ``duplicate_reads`` split by what the Agent last did to the path. Only
    # ``reread_after_edit`` is removable by giving edit_file a diff, so an A/B
    # on that fix must read this and not the conflated total. Both are None
    # when the artifact carries no event sequence — see attribute_rereads.
    reread_after_edit: int | None
    reread_after_read: int | None
    failed_reads: int  # unambiguous waste: dir-as-file / sensitive-path / missing
    relevant_hit: int
    recall: float
    right_file_edit: int
    gate_pass: bool  # edited >= 1 gold source file
    footprint: float | None  # None unless this run is scored (completed + gate)
    scored: bool


@dataclass
class TaskScore:
    task_id: str
    repo: str
    gold_source_n: int
    completed: int
    capped: int
    failed: int
    scored: int  # completed AND gate_pass — the footprint denominator population
    footprint_median: float | None
    recall_median: float | None
    runs: list[RunScore] = field(default_factory=list)


def attribute_rereads(run: RunRecord) -> tuple[int | None, int | None]:
    """Split duplicate reads by the last thing that touched the same path.

    Walks the ordered event sequence and, for each successful re-read, asks
    what the previous touch of that path was: an edit (the Agent verifying its
    own change) or a read (the Agent losing track of what it already had).

    Returns ``(None, None)`` when the run carries no event sequence — a
    pre-instrumentation artifact cannot be scored as zero without making it
    look like a flawless baseline.
    """
    if not run.events:
        return None, None
    after_edit = 0
    after_read = 0
    last_touch: dict[str, str] = {}
    for event in run.events:
        if event.is_error:
            continue  # a failed read is waste, but it is not a re-read
        if event.kind == "read":
            match last_touch.get(event.path):
                case "edit":
                    after_edit += 1
                case "read":
                    after_read += 1
        last_touch[event.path] = event.kind
    return after_edit, after_read


def score_run(run: RunRecord, relevant_files: list[str]) -> RunScore:
    ok_paths = [r.path for r in run.reads if not r.is_error]
    distinct = list(dict.fromkeys(ok_paths))
    failed = sum(1 for r in run.reads if r.is_error)
    gold = set(relevant_files)
    edits = set(run.edits)
    relevant_hit = len(gold & set(distinct))
    right_file_edit = len(gold & edits)
    gate_pass = right_file_edit >= 1
    completed = run.termination == "completed"
    scored = completed and gate_pass and bool(relevant_files)
    footprint = len(distinct) / len(relevant_files) if scored else None
    recall = relevant_hit / len(relevant_files) if relevant_files else 0.0
    after_edit, after_read = attribute_rereads(run)
    return RunScore(
        run_index=run.run_index,
        termination=run.termination,
        distinct_reads=len(distinct),
        duplicate_reads=len(ok_paths) - len(distinct),
        reread_after_edit=after_edit,
        reread_after_read=after_read,
        failed_reads=failed,
        relevant_hit=relevant_hit,
        recall=round(recall, 3),
        right_file_edit=right_file_edit,
        gate_pass=gate_pass,
        footprint=round(footprint, 3) if footprint is not None else None,
        scored=scored,
    )


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def score_task(task: TaskRuns) -> TaskScore:
    runs = [score_run(run, task.relevant_files) for run in task.runs]
    footprints = [r.footprint for r in runs if r.footprint is not None]
    recalls = [r.recall for r in runs if r.termination == "completed"]
    return TaskScore(
        task_id=task.task_id,
        repo=task.repo,
        gold_source_n=len(task.relevant_files),
        completed=sum(1 for r in runs if r.termination == "completed"),
        capped=sum(1 for r in runs if r.termination == "capped"),
        failed=sum(1 for r in runs if r.termination == "failed"),
        scored=sum(1 for r in runs if r.scored),
        footprint_median=_median(footprints),
        recall_median=_median(recalls),
        runs=runs,
    )


def aggregate(tasks: list[TaskScore]) -> dict[str, Any]:
    task_footprints = [t.footprint_median for t in tasks if t.footprint_median is not None]
    all_runs = [r for t in tasks for r in t.runs]
    scored_runs = [r for r in all_runs if r.scored]
    runs_with_failed = sum(1 for r in all_runs if r.failed_reads > 0)
    runs_with_dup = sum(1 for r in scored_runs if r.duplicate_reads > 0)
    attributed = [r for r in scored_runs if r.reread_after_edit is not None]
    reread_split: dict[str, int] | None = None
    if attributed:
        reread_split = {
            "after_edit": sum(r.reread_after_edit or 0 for r in attributed),
            "after_read": sum(r.reread_after_read or 0 for r in attributed),
            "runs_after_edit": sum(1 for r in attributed if r.reread_after_edit),
            "scored_runs_attributed": len(attributed),
        }

    footprint_stats: dict[str, float] | None = None
    if task_footprints:
        ordered = sorted(task_footprints)
        footprint_stats = {
            "median": round(statistics.median(ordered), 3),
            "min": round(ordered[0], 3),
            "max": round(ordered[-1], 3),
            "spread": round(ordered[-1] - ordered[0], 3),
        }
    return {
        "tasks": len(tasks),
        "runs_total": len(all_runs),
        "runs_completed": sum(1 for r in all_runs if r.termination == "completed"),
        "runs_capped": sum(1 for r in all_runs if r.termination == "capped"),
        "runs_failed": sum(1 for r in all_runs if r.termination == "failed"),
        "runs_scored": len(scored_runs),
        "footprint_over_task_medians": footprint_stats,
        "failed_read_runs": runs_with_failed,  # unambiguous-waste presence
        "duplicate_read_runs_scored": runs_with_dup,
        # None when no scored run carries an event sequence: the artifact
        # predates the instrumentation and cannot be used as an A/B arm.
        "reread_attribution": reread_split,
        # Construct-validity read of the pilot's success criteria:
        "construct_validity": {
            "waste_measurable_nonzero": runs_with_failed > 0 or runs_with_dup > 0,
            "footprint_spread_present": bool(footprint_stats and footprint_stats["spread"] > 0),
            "footprint_median_above_one": bool(footprint_stats and footprint_stats["median"] > 1.0),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    artifact = load_runs(args.runs)
    task_scores = [score_task(task) for task in artifact.tasks]
    summary = aggregate(task_scores)

    print(
        f"{'task_id':40} {'gold':>4} {'ok/cap/fail':>11} {'scored':>6} {'footp.':>7} {'recall':>6}"
    )
    print("-" * 88)
    for t in task_scores:
        term = f"{t.completed}/{t.capped}/{t.failed}"
        fp = "-" if t.footprint_median is None else f"{t.footprint_median:.2f}"
        rc = "-" if t.recall_median is None else f"{t.recall_median:.2f}"
        print(f"{t.task_id:40} {t.gold_source_n:>4} {term:>11} {t.scored:>6} {fp:>7} {rc:>6}")

    print("\n── aggregate ──")
    fp_stats = summary["footprint_over_task_medians"]
    if fp_stats:
        print(
            f"footprint (task medians): median={fp_stats['median']} "
            f"[{fp_stats['min']}..{fp_stats['max']}], spread={fp_stats['spread']}"
        )
    print(
        f"runs: {summary['runs_scored']} scored / "
        f"{summary['runs_completed']} completed / {summary['runs_capped']} capped / "
        f"{summary['runs_failed']} failed  (of {summary['runs_total']})"
    )
    print(
        f"waste: {summary['failed_read_runs']} runs with failed reads, "
        f"{summary['duplicate_read_runs_scored']} scored runs with duplicate reads"
    )
    split = summary["reread_attribution"]
    if split is None:
        print(
            "re-read attribution: unavailable — this artifact has no event "
            "sequence (recorded before the instrumentation); re-run to compare arms"
        )
    else:
        print(
            f"re-reads: {split['after_edit']} after an edit "
            f"({split['runs_after_edit']} runs), {split['after_read']} after a read "
            f"— over {split['scored_runs_attributed']} scored runs"
        )
    cv = summary["construct_validity"]
    print("construct validity:")
    print(f"  waste measurable & non-zero : {cv['waste_measurable_nonzero']}")
    print(f"  footprint spread present    : {cv['footprint_spread_present']}")
    print(f"  footprint median > 1.0      : {cv['footprint_median_above_one']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"summary": summary, "tasks": [asdict(t) for t in task_scores]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {args.out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
