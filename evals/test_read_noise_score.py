"""Deterministic checks for read-noise scoring (no model / no files).

Locks the gate + partition rules that keep the footprint metric honest: a Run
that doesn't edit a gold file, or that hit the cap, must not contribute a
footprint number.

    uv run pytest evals/test_read_noise_score.py -o addopts=""
"""

from __future__ import annotations

from evals.read_noise.schema import ReadRef, RunRecord, TaskRuns
from evals.read_noise.score import aggregate, score_run, score_task

GOLD = ["requests/sessions.py"]


def _run(idx: int, termination: str, reads: list[tuple[str, bool]], edits: list[str]) -> RunRecord:
    return RunRecord(
        run_index=idx,
        status=termination,
        termination=termination,
        model_calls=0,
        reads=[ReadRef(p, e) for p, e in reads],
        edits=edits,
        tool_calls=0,
        final_message="",
    )


def _task(runs: list[RunRecord]) -> TaskRuns:
    return TaskRuns(
        task_id="t",
        repo="psf/requests",
        base_commit="x",
        relevant_files=GOLD,
        added_files=[],
        runs=runs,
    )


def test_scored_run_computes_footprint() -> None:
    run = _run(
        0,
        "completed",
        [
            ("requests/sessions.py", False),
            ("requests/compat.py", False),
            ("requests/sessions.py", False),
        ],
        ["requests/sessions.py"],
    )
    score = score_run(run, GOLD)
    assert score.scored is True
    assert score.distinct_reads == 2
    assert score.footprint == 2.0  # 2 distinct / 1 gold
    assert score.duplicate_reads == 1  # sessions.py read twice
    assert score.recall == 1.0
    assert score.right_file_edit == 1


def test_completed_but_no_gold_edit_is_not_scored() -> None:
    run = _run(0, "completed", [("requests/sessions.py", False)], ["some/other.py"])
    score = score_run(run, GOLD)
    assert score.gate_pass is False
    assert score.scored is False
    assert score.footprint is None
    assert score.recall == 1.0  # recall still observed, just not gated in


def test_capped_run_never_scored() -> None:
    run = _run(0, "capped", [("requests/sessions.py", False)], ["requests/sessions.py"])
    score = score_run(run, GOLD)
    assert score.termination == "capped"
    assert score.scored is False
    assert score.footprint is None


def test_failed_reads_are_unambiguous_waste() -> None:
    run = _run(
        0,
        "completed",
        [("requests", True), ("/etc/passwd", True), ("requests/sessions.py", False)],
        ["requests/sessions.py"],
    )
    score = score_run(run, GOLD)
    assert score.failed_reads == 2  # directory-as-file + sensitive-path probe
    assert score.distinct_reads == 1
    assert score.footprint == 1.0


def test_task_median_over_scored_runs_only() -> None:
    task = _task(
        [
            _run(
                0,
                "completed",
                [("requests/sessions.py", False), ("a.py", False)],
                ["requests/sessions.py"],
            ),
            _run(1, "completed", [("requests/sessions.py", False)], ["other.py"]),  # gate fail
            _run(2, "completed", [("requests/sessions.py", False)], ["requests/sessions.py"]),
        ]
    )
    score = score_task(task)
    assert score.completed == 3
    assert score.scored == 2  # run 1 excluded (no gold edit)
    assert score.footprint_median == 1.5  # median of [2.0, 1.0]


def test_aggregate_construct_validity_flags() -> None:
    tasks = [
        score_task(
            _task(
                [
                    _run(
                        0,
                        "completed",
                        [("requests/sessions.py", False), ("x.py", False)],
                        ["requests/sessions.py"],
                    ),
                    _run(
                        1,
                        "completed",
                        [("requests", True), ("requests/sessions.py", False)],
                        ["requests/sessions.py"],
                    ),
                ]
            )
        ),
        score_task(
            _task(
                [_run(0, "completed", [("requests/sessions.py", False)], ["requests/sessions.py"])]
            )
        ),
    ]
    summary = aggregate(tasks)
    cv = summary["construct_validity"]
    assert cv["waste_measurable_nonzero"] is True  # a directory-as-file read exists
    assert cv["footprint_spread_present"] is True  # t1 median 2.0 vs t2 median 1.0
    assert cv["footprint_median_above_one"] is True
