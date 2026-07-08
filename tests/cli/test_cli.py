from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

import pytest
from typer.testing import CliRunner

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.cli import app
from milky_frog.cli.actions import build_doctor_diagnostics
from milky_frog.diagnostics import CheckStatus
from milky_frog.domain import RunStatus
from milky_frog.project import CONFIG_FILENAME, PROJECT_DIRNAME
from milky_frog.settings import Settings
from milky_frog.tui.app import TuiLaunch
from tests.checkpoint_helpers import seed_run

runner = CliRunner()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        api_key="test-key",
        model="test-model",
        base_url=None,
        _env_file=None,
    )


def _write_config(workspace: Path, body: str) -> None:
    root = workspace / PROJECT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILENAME).write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_cwd(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_require_model_config_validates_before_construction(tmp_path: Path) -> None:
    """Missing model config raises before any resource-holding object is built."""
    from milky_frog.app.session import AgentSession, MissingModelConfiguration
    from milky_frog.settings import Settings

    settings = Settings(home=tmp_path, api_key=None, model=None, base_url=None, _env_file=None)
    with pytest.raises(MissingModelConfiguration):
        AgentSession.require_model_configuration(settings)


def test_version_shows_version(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["--version"],
        env={"MILKY_FROG_HOME": str(tmp_path), "NO_COLOR": "1"},
    )

    assert result.exit_code == 0
    from milky_frog import __version__

    assert __version__ in result.stdout.strip()


def test_resume_without_task_opens_tui_with_pending_advance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launch_module = import_module("milky_frog.cli.launch")
    launches: list[TuiLaunch] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    seed_run(store, "run-abc", workspace, status=RunStatus.PAUSED_LIMIT)

    def fake_run_tui(settings: object, *, launch: TuiLaunch | None = None) -> None:
        del settings
        if launch is not None:
            launches.append(launch)

    monkeypatch.setattr(launch_module, "run_tui", fake_run_tui)
    result = runner.invoke(
        app,
        ["resume", "run-abc"],
        env={
            "MILKY_FROG_HOME": str(tmp_path),
            "MILKY_FROG_API_KEY": "test-key",
            "MILKY_FROG_MODEL": "test-model",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert launches == [
        TuiLaunch(run_id="run-abc", prompt=None, advance_pending=True),
    ]


def test_resume_with_task_opens_tui_with_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launch_module = import_module("milky_frog.cli.launch")
    launches: list[TuiLaunch] = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    seed_run(store, "run-abc", workspace, status=RunStatus.COMPLETED, final_message="done")

    def fake_run_tui(settings: object, *, launch: TuiLaunch | None = None) -> None:
        del settings
        if launch is not None:
            launches.append(launch)

    monkeypatch.setattr(launch_module, "run_tui", fake_run_tui)
    result = runner.invoke(
        app,
        ["resume", "run-abc", "follow up"],
        env={
            "MILKY_FROG_HOME": str(tmp_path),
            "MILKY_FROG_API_KEY": "test-key",
            "MILKY_FROG_MODEL": "test-model",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert launches == [
        TuiLaunch(run_id="run-abc", prompt="follow up", advance_pending=False),
    ]


def test_run_opens_tui_with_initial_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    launch_module = import_module("milky_frog.cli.launch")
    launches: list[TuiLaunch] = []

    def fake_run_tui(settings: object, *, launch: TuiLaunch | None = None) -> None:
        del settings
        if launch is not None:
            launches.append(launch)

    monkeypatch.setattr(launch_module, "run_tui", fake_run_tui)
    result = runner.invoke(
        app,
        ["run", "build feature x"],
        env={
            "MILKY_FROG_HOME": str(tmp_path),
            "MILKY_FROG_API_KEY": "test-key",
            "MILKY_FROG_MODEL": "test-model",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert launches == [TuiLaunch(prompt="build feature x")]


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
    assert "Required model configuration is missing." in result.stderr
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
    seed_run(store, "run-123", workspace, status=RunStatus.COMPLETED, final_message="done")

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
    assert "unknown Run: missing" in result.stderr
    assert "List available Runs with: milky-frog runs" in result.stderr


async def test_doctor_reports_local_sandbox_by_default(tmp_path: Path) -> None:
    diagnostics = await build_doctor_diagnostics(_settings(tmp_path), tmp_path)

    sandbox = next(d for d in diagnostics if d.name == "Sandbox")
    assert sandbox.status is CheckStatus.PASS
    assert sandbox.value == "local"


async def test_doctor_fails_when_docker_configured_but_unavailable(tmp_path: Path) -> None:
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\nimage = "python:3.12"\n')

    diagnostics = await build_doctor_diagnostics(
        _settings(tmp_path), tmp_path, docker_available=False
    )

    sandbox = next(d for d in diagnostics if d.name == "Sandbox")
    assert sandbox.status is CheckStatus.FAIL
    assert "docker" in sandbox.value.lower()


async def test_doctor_passes_when_docker_configured_and_available(tmp_path: Path) -> None:
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\nimage = "python:3.12"\n')

    diagnostics = await build_doctor_diagnostics(
        _settings(tmp_path), tmp_path, docker_available=True
    )

    sandbox = next(d for d in diagnostics if d.name == "Sandbox")
    assert sandbox.status is CheckStatus.PASS
    assert "python:3.12" in sandbox.value


async def test_doctor_fails_on_invalid_sandbox_table(tmp_path: Path) -> None:
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')  # no image

    diagnostics = await build_doctor_diagnostics(_settings(tmp_path), tmp_path)

    sandbox = next(d for d in diagnostics if d.name == "Sandbox")
    assert sandbox.status is CheckStatus.FAIL
    assert "image" in sandbox.value
