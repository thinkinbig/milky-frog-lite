"""Deterministic checks for the read-noise cross-stage schema (no model).

Locks the two things every stage depends on: the load/dump round-trips (the
typed seam between stages) and the shared vocabulary (termination bucketing,
read-path normalization).

    uv run pytest evals/test_read_noise_schema.py -o addopts=""
"""

from __future__ import annotations

from pathlib import Path

from evals.read_noise.schema import (
    ReadNoiseTask,
    ReadRef,
    RunRecord,
    RunsArtifact,
    TaskRuns,
    dump_runs,
    dump_tasks,
    load_runs,
    load_tasks,
    normalize_read_path,
    termination_kind,
)
from milky_frog.domain import RunStatus


def _task(task_id: str) -> ReadNoiseTask:
    return ReadNoiseTask(
        task_id=task_id,
        instance_id=task_id,
        repo="psf/requests",
        base_commit="deadbeef",
        problem_statement="fix it",
        relevant_files=["requests/sessions.py"],
        added_files=[],
        changed_files=[{"path": "requests/sessions.py", "status": "M"}],
    )


def test_tasks_roundtrip(tmp_path: Path) -> None:
    tasks = [_task("a-1"), _task("b-2")]
    out = tmp_path / "tasks.json"
    dump_tasks(tasks, out)
    assert load_tasks(out) == tasks


def test_runs_roundtrip_preserves_read_refs(tmp_path: Path) -> None:
    artifact = RunsArtifact(
        meta={"model": "m", "repeats": 1},
        tasks=[
            TaskRuns(
                task_id="a-1",
                repo="psf/requests",
                base_commit="deadbeef",
                relevant_files=["requests/sessions.py"],
                added_files=[],
                runs=[
                    RunRecord(
                        run_index=0,
                        status="completed",
                        termination="completed",
                        model_calls=8,
                        reads=[ReadRef("requests/sessions.py", False), ReadRef("x", True)],
                        edits=["requests/sessions.py"],
                        tool_calls=5,
                        final_message="done",
                    )
                ],
            )
        ],
    )
    out = tmp_path / "runs.json"
    dump_runs(artifact, out)
    loaded = load_runs(out)
    assert loaded.meta == {"model": "m", "repeats": 1}
    reads = loaded.tasks[0].runs[0].reads
    assert reads == [ReadRef("requests/sessions.py", False), ReadRef("x", True)]
    assert isinstance(reads[0], ReadRef)


def test_termination_buckets() -> None:
    assert termination_kind(RunStatus.COMPLETED) == "completed"
    assert termination_kind(RunStatus.PAUSED_LIMIT) == "capped"
    for status in (RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.WAITING_FOR_INPUT):
        assert termination_kind(status) == "failed"


def test_normalize_absolute_inside_workspace(tmp_path: Path) -> None:
    assert normalize_read_path(str(tmp_path / "pkg" / "a.py"), tmp_path) == "pkg/a.py"


def test_normalize_relative_strips_dot_slash(tmp_path: Path) -> None:
    assert normalize_read_path("./pkg/a.py", tmp_path) == "pkg/a.py"
    assert normalize_read_path("  pkg/a.py  ", tmp_path) == "pkg/a.py"


def test_normalize_outside_workspace_kept_verbatim(tmp_path: Path) -> None:
    # A sensitive-path probe lands outside the workspace; it must survive
    # normalization so the scorer can still flag it as waste.
    assert normalize_read_path("/etc/passwd", tmp_path) == "/etc/passwd"
