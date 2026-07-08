from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from stubs import StubDockerCli

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.docker.cli import DockerUnavailable
from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandTimeout,
    SandboxViolation,
)
from milky_frog.project import ProjectConfig


def _container_name(workspace: Path) -> str:
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"milky-frog-{digest}"


def _factory(cli: StubDockerCli, image: str = "python:3.12") -> DockerSandboxFactory:
    return DockerSandboxFactory(image=image, workspace_mount="/mnt/workspace", cli=cli)


async def test_run_command_creates_container_then_execs(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    outcome = await sandbox.run_command("echo hi", timeout_seconds=5)

    assert isinstance(outcome, CommandResult)
    run_argv = cli.captured[0]
    assert run_argv[:3] == ["docker", "run", "-d"]
    assert "-v" in run_argv
    assert f"{tmp_path.resolve()}:/mnt/workspace" in run_argv
    assert run_argv[-3:] == ["python:3.12", "sleep", "infinity"]

    exec_argv = cli.combined_calls[0].argv
    assert exec_argv[:2] == ["docker", "exec"]
    assert exec_argv[2:4] == ["-w", "/mnt/workspace"]
    assert exec_argv[-3:] == ["sh", "-c", "echo hi"]
    assert "abc123" in exec_argv


async def test_container_is_created_once_and_reused(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    await sandbox.run_command("echo one", timeout_seconds=5)
    await sandbox.run_command("echo two", timeout_seconds=5)

    docker_run_calls = [argv for argv in cli.captured if argv[:2] == ["docker", "run"]]
    assert len(docker_run_calls) == 1
    assert len(cli.combined_calls) == 2


async def test_run_command_forwards_timeout_to_cli(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    await sandbox.run_command("echo hi", timeout_seconds=12.5)

    assert cli.combined_calls[0].timeout_seconds == 12.5


async def test_run_command_surfaces_timeout(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123", outcome=CommandTimeout(seconds=3.0))
    sandbox = _factory(cli)(tmp_path)

    outcome = await sandbox.run_command("sleep 99", timeout_seconds=3)

    assert isinstance(outcome, CommandTimeout)
    assert outcome.seconds == 3.0


async def test_build_env_omits_host_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/Users/somebody")
    monkeypatch.setenv("PATH", "/host/bin")
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    env = sandbox.build_env()

    assert "HOME" not in env
    assert "PATH" not in env
    assert "SHELL" not in env
    assert env["CI"] == "true"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


async def test_build_env_forwards_allowlisted_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_BUILD_VAR", "secret-value")
    cli = StubDockerCli(container_id="abc123")
    factory = DockerSandboxFactory(
        image="python:3.12",
        workspace_mount="/mnt/workspace",
        cli=cli,
        config=ProjectConfig(env_allowlist_extra=("MY_BUILD_VAR",)),
    )
    sandbox = factory(tmp_path)

    assert sandbox.build_env()["MY_BUILD_VAR"] == "secret-value"


async def test_run_command_passes_env_as_exec_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    await sandbox.run_command("echo hi", timeout_seconds=5)

    exec_argv = cli.combined_calls[0].argv
    assert "-e" in exec_argv
    assert "CI=true" in exec_argv


async def test_terminal_presentation_adds_colour_env(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    await sandbox.run_command("ls", timeout_seconds=5, presentation=CommandPresentation.TERMINAL)

    exec_argv = cli.combined_calls[0].argv
    assert "FORCE_COLOR=1" in exec_argv


async def test_resolve_reuses_local_deny_policy(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    assert sandbox.resolve("src/app.py") == tmp_path / "src/app.py"
    with pytest.raises(SandboxViolation):
        sandbox.resolve(".env")
    with pytest.raises(SandboxViolation):
        sandbox.resolve("../secret")


async def test_aclose_stops_and_removes_started_containers(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    factory = _factory(cli)
    sandbox = factory(tmp_path)
    await sandbox.run_command("echo hi", timeout_seconds=5)

    await factory.aclose()

    assert ["docker", "rm", "-f", "abc123"] in cli.captured


async def test_aclose_is_idempotent(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    factory = _factory(cli)
    await factory(tmp_path).run_command("echo hi", timeout_seconds=5)

    await factory.aclose()
    await factory.aclose()

    removals = [argv for argv in cli.captured if argv[:2] == ["docker", "rm"]]
    assert len(removals) == 1


async def test_run_command_parses_id_past_a_banner_line(tmp_path: Path) -> None:
    cli = StubDockerCli(run_stdout="WARNING: something\nabc123\n")
    sandbox = _factory(cli)(tmp_path)

    outcome = await sandbox.run_command("echo hi", timeout_seconds=5)

    assert isinstance(outcome, CommandResult)
    exec_argv = cli.combined_calls[0].argv
    assert "abc123" in exec_argv
    assert "WARNING: something" not in exec_argv


async def test_start_cleans_up_container_when_id_is_unusable(tmp_path: Path) -> None:
    cli = StubDockerCli(run_stdout="   \n")
    sandbox = _factory(cli)(tmp_path)

    with pytest.raises(DockerUnavailable):
        await sandbox.run_command("echo hi", timeout_seconds=5)

    expected_name = _container_name(tmp_path.resolve())
    assert ["docker", "rm", "-f", expected_name] in cli.captured


async def test_acquire_after_aclose_raises_and_issues_no_new_run(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    factory = _factory(cli)
    sandbox = factory(tmp_path)
    await sandbox.run_command("echo hi", timeout_seconds=5)

    await factory.aclose()
    run_calls_before = len([argv for argv in cli.captured if argv[:2] == ["docker", "run"]])

    with pytest.raises(DockerUnavailable):
        await sandbox.run_command("echo two", timeout_seconds=5)

    run_calls_after = len([argv for argv in cli.captured if argv[:2] == ["docker", "run"]])
    assert run_calls_after == run_calls_before
