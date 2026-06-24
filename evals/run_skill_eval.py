"""Smoke eval: does the agent discover and read the tdd skill when asked?

Requires MILKY_FROG_API_KEY and MILKY_FROG_MODEL in env or .env.

    uv run python -m evals.run_skill_eval
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from evals._settings import without_observability
from evals.read_collector import ReadCollector
from milky_frog.agent_session import AgentSession
from milky_frog.handlers import EventDispatcher
from milky_frog.project import PROJECT_DIRNAME
from milky_frog.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "evals" / "datasets" / "skill_recognition.json"
FIXTURE_SKILL = REPO_ROOT / "tests" / "fixtures" / "skills" / "tdd" / "SKILL.md"


def _install_skill(workspace: Path) -> Path:
    dest = workspace / PROJECT_DIRNAME / "skills" / "tdd" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_SKILL, dest)
    (workspace / PROJECT_DIRNAME / "config.toml").write_text("max_model_calls = 6\n")
    return dest


def _normalize(path: str, workspace: Path) -> str:
    candidate = Path(path.strip())
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            return path
    return candidate.as_posix().removeprefix("./")


async def _run_task_async(settings: Settings, task: dict[str, object]) -> dict[str, object]:
    workspace = Path(tempfile.mkdtemp(prefix="mf-skill-eval-"))
    try:
        skill_path = _install_skill(workspace)
        expected_read = str(task["expect_skill_read"])

        bus = EventDispatcher()
        collector = ReadCollector()
        async with AgentSession.from_settings(
            settings, handlers=bus, bundles=[collector]
        ) as session:
            session.policy.auto_approve()
            result = await session.start_new(str(task["prompt"]), workspace)

        reads = [
            _normalize(record.path, workspace) for record in collector.reads.get(result.run_id, [])
        ]
        read_skill = any(expected_read in path or path.endswith("tdd/SKILL.md") for path in reads)
        mentioned = "tdd" in result.final_message.lower()

        return {
            "task_id": task["task_id"],
            "status": result.status.value,
            "read_skill": read_skill,
            "mentioned_skill": mentioned,
            "reads": reads,
            "skill_path": skill_path.relative_to(workspace).as_posix(),
            "final_message_preview": result.final_message[:400],
            "passed": read_skill and mentioned,
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_task(settings: Settings, task: dict[str, object]) -> dict[str, object]:
    return asyncio.run(_run_task_async(settings, task))


def main() -> None:
    tasks = json.loads(DATASET.read_text(encoding="utf-8"))
    settings = without_observability(Settings.from_environment())
    results = [run_task(settings, task) for task in tasks]
    passed = sum(1 for result in results if result["passed"])
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
