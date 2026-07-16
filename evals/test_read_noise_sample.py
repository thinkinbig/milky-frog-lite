"""Deterministic checks for the read-noise sampler (no network / no datasets).

Exercises the three pure functions — patch parsing, the tight-patch filter, and
the round-robin stratifier — with hand-built git diffs.

    uv run pytest evals/test_read_noise_sample.py -o addopts=""
"""

from __future__ import annotations

import random
from typing import Any

from evals.read_noise.sample import _is_source, eligible_task, parse_patch, stratify
from evals.read_noise.schema import ReadNoiseTask

# --- diff fixtures ---------------------------------------------------------


def _modify(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 1111111..2222222 100644\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,2 +1,2 @@\n-old\n+new\n"
    )


def _add(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\nindex 0000000..3333333\n"
        f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,1 @@\n+a\n"
    )


def _delete(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"deleted file mode 100644\nindex 4444444..0000000\n"
        f"--- a/{path}\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-a\n"
    )


def _rename(old: str, new: str) -> str:
    return (
        f"diff --git a/{old} b/{new}\n"
        f"similarity index 90%\nrename from {old}\nrename to {new}\n"
        f"index 5555555..6666666 100644\n--- a/{old}\n+++ b/{new}\n"
    )


def _row(patch: str, **over: Any) -> dict[str, Any]:
    row = {
        "patch": patch,
        "instance_id": "acme__lib-1",
        "repo": "acme/lib",
        "base_commit": "deadbeef",
        "problem_statement": "fix the thing",
    }
    row.update(over)
    return row


# --- parse_patch -----------------------------------------------------------


def test_parse_patch_classifies_each_status() -> None:
    patch = (
        _modify("pkg/a.py")
        + _add("pkg/b.py")
        + _delete("pkg/c.py")
        + _rename("pkg/d.py", "pkg/e.py")
    )
    by_path = {c.path: c.status for c in parse_patch(patch)}
    assert by_path == {"pkg/a.py": "M", "pkg/b.py": "A", "pkg/c.py": "D", "pkg/e.py": "R"}


def test_parse_patch_ignores_leading_noise() -> None:
    assert [c.path for c in parse_patch("preamble text\n" + _modify("pkg/a.py"))] == ["pkg/a.py"]


# --- _is_source ------------------------------------------------------------


def test_is_source_true_for_plain_module() -> None:
    assert _is_source("django/utils/html.py") is True


def test_is_source_false_for_tests_and_non_python() -> None:
    assert _is_source("tests/test_html.py") is False
    assert _is_source("pkg/test_util.py") is False
    assert _is_source("pkg/util_test.py") is False
    assert _is_source("pkg/conftest.py") is False
    assert _is_source("pkg/testing/helpers.py") is False
    assert _is_source("docs/guide.rst") is False


# --- eligible_task ---------------------------------------------------------


def test_eligible_single_modified_source() -> None:
    task = eligible_task(_row(_modify("pkg/a.py")), min_src=1, max_src=6, max_total=12)
    assert task is not None
    assert task.relevant_files == ["pkg/a.py"]
    assert task.added_files == []


def test_eligible_splits_relevant_from_added() -> None:
    task = eligible_task(
        _row(_modify("pkg/a.py") + _add("pkg/b.py")), min_src=1, max_src=6, max_total=12
    )
    assert task is not None
    assert task.relevant_files == ["pkg/a.py"]
    assert task.added_files == ["pkg/b.py"]


def test_test_files_do_not_count_as_source() -> None:
    patch = _modify("pkg/a.py") + _modify("tests/test_a.py") + _add("tests/test_b.py")
    task = eligible_task(_row(patch), min_src=1, max_src=6, max_total=12)
    assert task is not None
    assert task.relevant_files == ["pkg/a.py"]


def test_reject_no_source_touched() -> None:
    patch = _modify("tests/test_a.py") + _modify("docs/guide.rst")
    assert eligible_task(_row(patch), min_src=1, max_src=6, max_total=12) is None


def test_reject_above_max_src() -> None:
    patch = _modify("pkg/a.py") + _modify("pkg/b.py") + _modify("pkg/c.py")
    assert eligible_task(_row(patch), min_src=1, max_src=2, max_total=12) is None


def test_reject_above_max_total() -> None:
    patch = (
        _modify("pkg/a.py") + _modify("docs/x.rst") + _modify("docs/y.rst") + _modify("docs/z.rst")
    )
    assert eligible_task(_row(patch), min_src=1, max_src=6, max_total=3) is None


def test_reject_rename_churn() -> None:
    assert (
        eligible_task(_row(_rename("pkg/a.py", "pkg/b.py")), min_src=1, max_src=6, max_total=12)
        is None
    )


def test_reject_source_deleted_only() -> None:
    assert eligible_task(_row(_delete("pkg/a.py")), min_src=1, max_src=6, max_total=12) is None


# --- stratify --------------------------------------------------------------


def _tasks(repo: str, count: int) -> list[ReadNoiseTask]:
    return [
        ReadNoiseTask(
            task_id=f"{repo}-{i}",
            instance_id=f"{repo}-{i}",
            repo=repo,
            base_commit="x",
            problem_statement="p",
            relevant_files=["pkg/a.py"],
            added_files=[],
            changed_files=[{"path": "pkg/a.py", "status": "M"}],
        )
        for i in range(count)
    ]


def _by_repo() -> dict[str, list[ReadNoiseTask]]:
    return {"a/a": _tasks("a/a", 10), "b/b": _tasks("b/b", 10), "c/c": _tasks("c/c", 2)}


def test_stratify_respects_per_repo_cap_and_size() -> None:
    picked = stratify(_by_repo(), size=8, per_repo_cap=3, rng=random.Random(0))
    assert len(picked) == 8
    per_repo: dict[str, int] = {}
    for task in picked:
        per_repo[task.repo] = per_repo.get(task.repo, 0) + 1
    assert all(count <= 3 for count in per_repo.values())
    assert per_repo == {"a/a": 3, "b/b": 3, "c/c": 2}


def test_stratify_is_seed_deterministic() -> None:
    first = stratify(_by_repo(), size=8, per_repo_cap=3, rng=random.Random(13))
    second = stratify(_by_repo(), size=8, per_repo_cap=3, rng=random.Random(13))
    assert [t.task_id for t in first] == [t.task_id for t in second]


def test_stratify_stops_when_supply_exhausted() -> None:
    picked = stratify({"a/a": _tasks("a/a", 4)}, size=10, per_repo_cap=6, rng=random.Random(0))
    assert len(picked) == 4
