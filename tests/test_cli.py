from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

from milky_frog.checkpoint import RunEvent, SqliteCheckpointStore
from milky_frog.cli import app
from milky_frog.domain import RunResult, RunStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path_factory: pytest.TempPathFactory,
 monkeypatch: pytest.MonkeyPatch) -> None:


    """Run each CLI test in an empty cwd so a developer's .env never leaks in.

    Settings.from_environment() reads ``<cwd>/.env``; without isolation the
    repository's own .env would supply configuration these tests assume absent.
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


def test_help_includes_compact_brand() -> None:
    result = runner.invoke(app, ["--help"], env={"NO_COLOR": "1"})

    assert result.exit_code == 0
    assert "MILKY FROG" in result.stdout
    assert "奶蛙" in result.stdout


def test_init_creates_a_not_yet_existing_workspace(tmp_path: Path) -> None:
    target = tmp_path / "new" / "project"

    result = runner.invoke(app, ["init", str(target)], env={"NO_COLOR": "1"})

    assert result.exit_code == 0
    assert (target / ".milky-frog" / "config.toml").is_file()
    assert (target / ".milky-frog" / "skills").is_dir()


def test_init_reports_filesystem_error_without_traceback(tmp_path: Path) -> None:
    blocker = tmp_path / "occupied"
    blocker.write_text("not a directory", encoding="utf-8")

    result = runner.invoke(app, ["init", str(blocker)], env={"NO_COLOR": "1"})

    assert result.exit_code == 1
    assert "Could not initialize workspace" in result.stderr
    assert "Traceback" not in result.stderr


def test_no_arguments_starts_interactive_mode(monkeypatch: object, tmp_path: Path) -> None:
    cli_module = import_module("milky_frog.cli.app")

    class FakeMilkyFrog:
        @classmethod
        def from_settings(cls, settings: object) -> FakeMilkyFrog:
            del settings
            return cls()

        def run(self, task: str, workspace: Path) -> RunResult:
            assert workspace.is_dir()
            assert task == "hello frog"
            return RunResult("run-interactive", RunStatus.COMPLETED, "hello human", 1)

    monkeypatch.setattr(cli_module, "MilkyFrog", FakeMilkyFrog)  # type: ignore[attr-defined]
    result = runner.invoke(
        app,
        input="/help\nhello frog\nquit\n",
        env={
            "MILKY_FROG_HOME": str(tmp_path),
            "MILKY_FROG_API_KEY": "test-key",
            "MILKY_FROG_MODEL": "test-model",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert "MILKY FROG · 奶蛙" in result.stdout
    assert "hello human" in result.stdout
    assert "run-inte" in result.stdout
    assert "test-model" in result.stdout
    assert "/clear" in result.stdout


def test_no_arguments_requires_model_configuration(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 2
    assert "Required model configuration is missing" in result.stderr


def test_doctor_keeps_results_on_stdout_and_errors_on_stderr(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["doctor"],
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 2
    assert "Milky Frog doctor" in result.stdout
    assert "FAIL" in result.stdout
    assert "Error: Required model configuration is missing." in result.stderr
    assert "API key" not in result.stderr


def test_runs_shows_empty_state(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["runs"],
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert "No runs yet." in result.stdout
    assert result.stderr == ""


def test_show_json_is_clean_machine_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    store.create_run("run-123", workspace)
    store.append(
        "run-123",
        RunEvent("RunCompleted", {"final_message": "done"}),
        RunStatus.COMPLETED,
    )

    result = runner.invoke(
        app,
        ["show", "run-123", "--json"],
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout)["run_id"] == "run-123"


def test_unknown_run_is_reported_on_stderr(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["show", "missing"],
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: Unknown Run: missing" in result.stderr
    assert "Hint: List available Runs with: milky-frog runs" in result.stderr
