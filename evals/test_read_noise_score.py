"""Deterministic checks for read-noise scoring (no model / no files).

Locks the gate + partition rules that keep the footprint metric honest: a Run
that doesn't edit a gold file, or that hit the cap, must not contribute a
footprint number.

    uv run pytest evals/test_read_noise_score.py -o addopts=""
"""

from __future__ import annotations

from evals.read_noise.schema import FileEvent, ReadRef, RunRecord, TaskRuns
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


# --- re-read attribution ----------------------------------------------------
#
# ``duplicate_reads`` counts a path read more than once, but conflates two
# behaviours with different causes and different fixes:
#   * read -> edit -> read   the Agent verifying its own edit (edit_file's
#                            affordance gap, #108 cause 1)
#   * read -> ... -> read    the Agent losing track of what it already saw
# Only the first is what returning a diff from edit_file can remove, so the
# A/B arms must be able to tell them apart.


def _seq(idx: int, termination: str, events: list[tuple[str, str, bool]]) -> RunRecord:
    """Build a RunRecord from one ordered (kind, path, is_error) sequence."""
    file_events = [FileEvent(kind, path, is_error) for kind, path, is_error in events]
    return RunRecord(
        run_index=idx,
        status=termination,
        termination=termination,
        model_calls=0,
        events=file_events,
        reads=[ReadRef(e.path, e.is_error) for e in file_events if e.kind == "read"],
        edits=[e.path for e in file_events if e.kind == "edit"],
        tool_calls=0,
        final_message="",
    )


def test_read_after_own_edit_is_attributed_to_the_edit() -> None:
    run = _seq(
        0,
        "completed",
        [
            ("read", "requests/sessions.py", False),
            ("edit", "requests/sessions.py", False),
            ("read", "requests/sessions.py", False),
        ],
    )
    score = score_run(run, GOLD)
    assert score.duplicate_reads == 1
    assert score.reread_after_edit == 1
    assert score.reread_after_read == 0


def test_read_after_read_is_not_attributed_to_an_edit() -> None:
    """The same duplicate count, a different cause — edit_file's diff can't help."""
    run = _seq(
        0,
        "completed",
        [
            ("read", "requests/sessions.py", False),
            ("read", "requests/compat.py", False),
            ("read", "requests/sessions.py", False),
            ("edit", "requests/sessions.py", False),
        ],
    )
    score = score_run(run, GOLD)
    assert score.duplicate_reads == 1
    assert score.reread_after_edit == 0
    assert score.reread_after_read == 1


def test_reread_attribution_partitions_duplicate_reads() -> None:
    """The two attributions are disjoint and account for every duplicate."""
    run = _seq(
        0,
        "completed",
        [
            ("read", "a.py", False),
            ("read", "a.py", False),  # after read
            ("edit", "a.py", False),
            ("read", "a.py", False),  # after edit
            ("read", "b.py", False),
            ("edit", "b.py", False),
            ("read", "b.py", False),  # after edit
        ],
    )
    score = score_run(run, GOLD)
    assert score.duplicate_reads == 3
    assert score.reread_after_edit == 2
    assert score.reread_after_read == 1
    assert score.reread_after_edit + score.reread_after_read == score.duplicate_reads


def test_attribution_is_unavailable_when_the_artifact_has_no_event_sequence() -> None:
    """A pre-instrumentation artifact must report None, never a misleading 0.

    The stored baseline predates the event sequence. Scoring it as
    ``reread_after_edit == 0`` would make it look like a perfect "before" arm
    and invert the A/B result.
    """
    run = _run(
        0,
        "completed",
        [("requests/sessions.py", False), ("requests/sessions.py", False)],
        ["requests/sessions.py"],
    )
    score = score_run(run, GOLD)
    assert score.duplicate_reads == 1  # still countable without ordering
    assert score.reread_after_edit is None
    assert score.reread_after_read is None


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
