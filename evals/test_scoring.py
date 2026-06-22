"""Deterministic checks for read-noise scoring (no model needed).

Run standalone (the prod pytest config targets tests/ with a coverage gate):
    uv run pytest evals/test_scoring.py -o addopts=""
"""

from __future__ import annotations

from evals.read_collector import ReadRecord
from evals.scoring import relevant_dirs, score_run

CHANGED = ["src/milky_frog/cli/app.py", "src/milky_frog/ui/interactive.py"]
RELEVANT = ["src/milky_frog/cli/app.py", "src/milky_frog/ui/interactive.py"]


def test_relevant_dirs_drops_root_files() -> None:
    assert relevant_dirs(["README.md", "src/pkg/a.py"]) == {"src/pkg"}


def test_in_scope_reads_score_clean() -> None:
    reads = [
        ReadRecord("src/milky_frog/cli/app.py", False),  # exact changed file
        ReadRecord("src/milky_frog/cli/factory.py", False),  # sibling in cli/ -> in scope
        ReadRecord("src/milky_frog/ui/interactive.py", False),  # changed file
    ]
    score = score_run(
        "t",
        reads,
        edits=["src/milky_frog/cli/app.py"],
        changed_files=CHANGED,
        relevant_files=RELEVANT,
    )
    assert score.scope_precision == 1.0
    assert score.noise_rate == 0.0
    assert score.out_of_scope == 0
    assert score.completed is True


def test_cross_package_reads_count_as_noise() -> None:
    reads = [
        ReadRecord("src/milky_frog/cli/app.py", False),  # in scope
        ReadRecord("src/milky_frog/ui/presenter/_messages.py", False),  # noise
        ReadRecord("src/milky_frog/models/openai.py", False),  # noise
        ReadRecord("src/milky_frog/cli/app.py", False),  # duplicate, in scope
    ]
    score = score_run("t", reads, edits=[], changed_files=CHANGED, relevant_files=RELEVANT)
    assert score.reads_total == 4
    assert score.reads_ok_unique == 3
    assert score.in_scope == 1
    assert score.out_of_scope == 2
    assert score.scope_precision == round(1 / 3, 3)
    assert score.duplicate_reads == 1
    assert score.completed is False
    assert score.reads_per_edit is None
    assert set(score.out_of_scope_paths) == {
        "src/milky_frog/ui/presenter/_messages.py",
        "src/milky_frog/models/openai.py",
    }


def test_failed_reads_excluded_from_precision_but_counted() -> None:
    reads = [
        ReadRecord("src/milky_frog/cli/app.py", False),  # ok, in scope
        ReadRecord("src/milky_frog/cli", True),  # directory-as-file -> failed
        ReadRecord("/repo/.env", True),  # sensitive -> failed
    ]
    score = score_run("t", reads, edits=[], changed_files=CHANGED, relevant_files=RELEVANT)
    assert score.failed_reads == 2
    assert score.reads_ok_unique == 1
    assert score.scope_precision == 1.0  # the one successful read was in scope
    assert score.completed is False


def test_relevant_recall() -> None:
    reads = [ReadRecord("src/milky_frog/cli/app.py", False)]  # read 1 of 2 relevant
    score = score_run("t", reads, edits=[], changed_files=CHANGED, relevant_files=RELEVANT)
    assert score.relevant_hit == 1
    assert score.relevant_total == 2
