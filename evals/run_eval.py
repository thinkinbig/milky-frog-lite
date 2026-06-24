"""Run the truncation eval: test agent resilience against massive outputs.

Usage:
    uv run python -m evals.run_eval
    uv run python -m evals.run_eval --dataset hard
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals._settings import without_observability
from evals.tool_collector import ToolCallCollector, ToolCallRecord, summarize_tool_call
from milky_frog.agent_session import AgentSession
from milky_frog.handlers import EventHub
from milky_frog.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS = {
    "basic": REPO_ROOT / "evals" / "datasets" / "change_tasks.json",
    "hard": REPO_ROOT / "evals" / "datasets" / "change_tasks_hard.json",
}
RESULTS = REPO_ROOT / "evals" / "results"


def matches_final_message(
    message: str,
    expected: str | list[str],
    *,
    match: str = "any",
) -> bool:
    """Return whether expected substring(s) appear in the final message."""
    haystack = message.lower()
    patterns = [expected] if isinstance(expected, str) else expected
    checks = [pattern.lower() in haystack for pattern in patterns]
    if match == "all":
        return bool(checks) and all(checks)
    return any(checks)


def task_passed(task: dict[str, Any], final_message: str) -> bool:
    expected = task["expected_in_final_message"]
    match = str(task.get("expected_match", "any"))
    return matches_final_message(final_message, expected, match=match)


def format_expected(expected: str | list[str]) -> str:
    if isinstance(expected, str):
        return expected
    return " | ".join(expected)


def serialize_tool_calls(records: list[ToolCallRecord]) -> list[dict[str, Any]]:
    return [
        {
            "name": record.name,
            "arguments": record.arguments,
            "is_error": record.is_error,
            "summary": summarize_tool_call(record),
        }
        for record in records
    ]


@dataclass
class EvalScore:
    task_id: str
    passed: bool
    final_message: str
    expected: str
    model_calls: int
    tool_calls: list[dict[str, Any]]


async def _run_task_async(
    settings: Settings, task: dict[str, Any], max_model_calls: int
) -> EvalScore:
    workspace = Path(tempfile.mkdtemp(prefix="mf-eval-truncation-"))

    if "setup_script" in task:
        subprocess.run(task["setup_script"], shell=True, cwd=workspace, check=True)

    try:
        cfg = workspace / ".milky-frog"
        cfg.mkdir(exist_ok=True)
        (cfg / "config.toml").write_text(f"max_model_calls = {max_model_calls}\n")

        bus = EventHub()
        collector = ToolCallCollector()
        async with AgentSession.from_settings(settings, hub=bus, bundles=[collector]) as session:
            session.policy.auto_approve()
            result = await session.start_new(task["prompt"], workspace)

        records = collector.calls.get(result.run_id, [])
        passed = task_passed(task, result.final_message)
        return EvalScore(
            task_id=task["task_id"],
            passed=passed,
            final_message=result.final_message,
            expected=format_expected(task["expected_in_final_message"]),
            model_calls=result.model_calls,
            tool_calls=serialize_tool_calls(records),
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_task(settings: Settings, task: dict[str, Any], max_model_calls: int) -> EvalScore:
    return asyncio.run(_run_task_async(settings, task, max_model_calls))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="basic", help="task set")
    ap.add_argument("--limit", type=int, default=None, help="only the first N tasks")
    ap.add_argument("--task", type=str, default=None, help="run a single task_id")
    ap.add_argument("--max-model-calls", type=int, default=12, help="cap per Run")
    args = ap.parse_args()

    settings = Settings.from_environment()
    dataset = args.dataset
    tasks = json.loads(DATASETS[dataset].read_text())
    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
    if args.limit:
        tasks = tasks[: args.limit]

    scores: list[EvalScore] = []
    eval_settings = without_observability(settings)
    for task in tasks:
        print(f"▶ {task['task_id']} …", flush=True)
        started = time.perf_counter()
        score = run_task(eval_settings, task, args.max_model_calls)
        scores.append(score)
        elapsed = time.perf_counter() - started
        print(
            f"    {'✓ PASS' if score.passed else '✗ FAIL'}"
            f"  ({score.model_calls} model calls, {len(score.tool_calls)} tools, {elapsed:.1f}s)"
        )
        for index, call in enumerate(score.tool_calls, start=1):
            print(f"      {index}. {call['summary']}")
        if not score.passed:
            print(f"    Expected: {score.expected}")
            print(f"    Got: {score.final_message}")

    if not scores:
        print("no tasks matched")
        return

    passed = [s for s in scores if s.passed]
    print("\n── aggregate ──")
    print(f"runs={len(scores)}  passed={len(passed)}  accuracy={len(passed) / len(scores):.2f}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / ("latest.json" if dataset == "basic" else f"latest-{dataset}.json")
    out.write_text(json.dumps([asdict(s) for s in scores], ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
