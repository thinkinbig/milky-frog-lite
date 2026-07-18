"""The owned interface between the read-noise stages.

The three stages communicate *only* through the typed artifacts defined here
plus their ``load``/``dump`` helpers — never through raw JSON dicts. The dataset
and run-record schemas therefore live in exactly one place: a field change is a
single edit, and every stage + test crosses the same typed seam.

Two artifacts flow through the pipeline:

    sample -> [tasks JSON]  -> run -> [runs JSON] -> score

``ReadNoiseTask`` is the first; ``RunsArtifact`` (tasks each holding ``RunRecord``s)
is the second. ``score`` consumes the second and emits a terminal scores file
whose shape it owns itself (not a cross-stage contract).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from milky_frog.domain import RunStatus

# --- task artifact: sample -> run ------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadNoiseTask:
    task_id: str
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    relevant_files: list[str]  # modified non-test source — the read/recall targets
    added_files: list[str]  # new non-test source — created, not read
    changed_files: list[dict[str, str]]


def dump_tasks(tasks: list[ReadNoiseTask], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2) + "\n")


def load_tasks(path: Path) -> list[ReadNoiseTask]:
    return [ReadNoiseTask(**entry) for entry in json.loads(path.read_text(encoding="utf-8"))]


# --- run artifact: run -> score --------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadRef:
    path: str  # workspace-relative
    is_error: bool


@dataclass(frozen=True, slots=True)
class FileEvent:
    """One file-touching Tool call, in the order the Run made it.

    ``reads`` and ``edits`` below are flat per-kind projections and cannot say
    whether a read came *before* or *after* an edit of the same path. That
    ordering is the whole question for the edit->re-read pathology, so the
    ordered sequence is recorded as its own field and the projections are kept
    for the metrics that genuinely don't need order (footprint, failed reads).
    """

    kind: str  # read | edit
    path: str  # workspace-relative
    is_error: bool


@dataclass
class RunRecord:
    run_index: int
    status: str  # RunStatus value
    termination: str  # completed | capped | failed (see termination_kind)
    model_calls: int
    reads: list[ReadRef]
    edits: list[str]
    tool_calls: int
    final_message: str
    events: list[FileEvent] = field(default_factory=list)
    """Ordered file-touch sequence; empty for artifacts recorded before this existed."""


@dataclass
class TaskRuns:
    task_id: str
    repo: str
    base_commit: str
    relevant_files: list[str]
    added_files: list[str]
    runs: list[RunRecord]


@dataclass
class RunsArtifact:
    meta: dict[str, object]
    tasks: list[TaskRuns]


def dump_runs(artifact: RunsArtifact, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(artifact), ensure_ascii=False, indent=2) + "\n")


def load_runs(path: Path) -> RunsArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = [
        TaskRuns(
            task_id=t["task_id"],
            repo=t["repo"],
            base_commit=t["base_commit"],
            relevant_files=t["relevant_files"],
            added_files=t["added_files"],
            runs=[
                RunRecord(
                    run_index=r["run_index"],
                    status=r["status"],
                    termination=r["termination"],
                    model_calls=r["model_calls"],
                    reads=[ReadRef(**ref) for ref in r["reads"]],
                    edits=r["edits"],
                    tool_calls=r["tool_calls"],
                    final_message=r["final_message"],
                    # Absent in artifacts recorded before the sequence existed;
                    # the scorer reports re-read attribution as unavailable
                    # rather than zero when it is missing.
                    events=[FileEvent(**e) for e in r.get("events", [])],
                )
                for r in t["runs"]
            ],
        )
        for t in payload["tasks"]
    ]
    return RunsArtifact(meta=payload["meta"], tasks=tasks)


# --- shared vocabulary ------------------------------------------------------


def termination_kind(status: RunStatus) -> str:
    """Bucket a terminal RunStatus for the scorer's partition-by-termination."""
    if status == RunStatus.COMPLETED:
        return "completed"  # agent stopped on its own — "reads to complete" is valid
    if status == RunStatus.PAUSED_LIMIT:
        return "capped"  # hit max_model_calls — read count is truncated, own bucket
    return "failed"  # errored / stuck waiting — not a data point for the metric


def normalize_read_path(path: str, workspace: Path) -> str:
    """Map a tool-call path to a workspace-relative POSIX path for gold matching."""
    candidate = Path(path.strip())
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()  # outside the workspace (e.g. a /etc probe)
    return candidate.as_posix().removeprefix("./")
