from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from stubs import (
    CancellingRunDockerCli,
    FailingRemoveDockerCli,
    SlowRunDockerCli,
    StubDockerCli,
)

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.docker.cli import DockerUnavailable
from milky_frog.adapters.docker.sandbox import WORKSPACE_LABEL
from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandTimeout,
    SandboxViolation,
)
from milky_frog.project import ProjectConfig


def _workspace_hash12(workspace: Path) -> str:
    return hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]


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

    expected_hash12 = _workspace_hash12(tmp_path.resolve())
    name_index = run_argv.index("--name")
    used_name = run_argv[name_index + 1]
    assert used_name.startswith(f"milky-frog-{expected_hash12}-")
    assert len(used_name) > len(f"milky-frog-{expected_hash12}-")

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

    run_argv = cli.captured[0]
    name_index = run_argv.index("--name")
    used_name = run_argv[name_index + 1]
    assert ["docker", "rm", "-f", used_name] in cli.captured


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


async def test_different_workspaces_get_different_container_names(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    workspace_a = tmp_path_factory.mktemp("workspace-a")
    workspace_b = tmp_path_factory.mktemp("workspace-b")
    cli = StubDockerCli(container_id="abc123")
    factory = _factory(cli)

    await factory(workspace_a).run_command("echo hi", timeout_seconds=5)
    await factory(workspace_b).run_command("echo hi", timeout_seconds=5)

    run_calls = [argv for argv in cli.captured if argv[:2] == ["docker", "run"]]
    assert len(run_calls) == 2
    names = [argv[argv.index("--name") + 1] for argv in run_calls]
    assert names[0] != names[1]
    assert all(name.startswith("milky-frog-") for name in names)


async def test_run_command_labels_container_with_workspace_hash(tmp_path: Path) -> None:
    cli = StubDockerCli(container_id="abc123")
    sandbox = _factory(cli)(tmp_path)

    await sandbox.run_command("echo hi", timeout_seconds=5)

    expected_hash12 = _workspace_hash12(tmp_path.resolve())
    run_argv = cli.captured[0]
    assert "--label" in run_argv
    label_index = run_argv.index("--label")
    assert run_argv[label_index + 1] == f"{WORKSPACE_LABEL}={expected_hash12}"


async def test_start_cleans_up_container_on_nonzero_exit(tmp_path: Path) -> None:
    cli = StubDockerCli(run_exit_code=1, run_stderr="no such image")
    sandbox = _factory(cli)(tmp_path)

    with pytest.raises(DockerUnavailable):
        await sandbox.run_command("echo hi", timeout_seconds=5)

    run_argv = cli.captured[0]
    assert run_argv[:2] == ["docker", "run"]
    name_index = run_argv.index("--name")
    used_name = run_argv[name_index + 1]
    assert ["docker", "rm", "-f", used_name] in cli.captured


async def test_aclose_removes_a_container_for_every_workspace(tmp_path: Path) -> None:
    """A single rm -f is not enough: the registry is keyed by Workspace."""
    cli = StubDockerCli(container_id="c", unique_ids=True)
    factory = _factory(cli)
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    await factory(first).run_command("echo hi", timeout_seconds=5)
    await factory(second).run_command("echo hi", timeout_seconds=5)
    assert cli.run_count() == 2

    await factory.aclose()

    assert sorted(cli.removed_ids()) == ["c-1", "c-2"]


async def test_aclose_keeps_removing_after_a_removal_fails(tmp_path: Path) -> None:
    """aclose() exists to clean up under failure; one bad rm must not strand the rest.

    `_containers` is cleared before removal, so a container skipped here is
    leaked permanently — nothing else holds a reference to retry it.
    """
    cli = FailingRemoveDockerCli(container_id="c", unique_ids=True)
    factory = _factory(cli)
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    await factory(first).run_command("echo hi", timeout_seconds=5)
    await factory(second).run_command("echo hi", timeout_seconds=5)

    await factory.aclose()

    assert sorted(cli.removed_ids()) == ["c-1", "c-2"]


async def test_cancelled_docker_run_removes_the_container_by_name(tmp_path: Path) -> None:
    """Ctrl-C mid-`docker run -d`: the daemon may have created the container
    while the client never returned an id, so nothing tracks it. `_start` must
    reap it by name or it is orphaned forever."""
    cli = CancellingRunDockerCli()
    factory = _factory(cli)
    sandbox = factory(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await sandbox.run_command("echo hi", timeout_seconds=5)

    removed = cli.removed_ids()
    assert len(removed) == 1
    assert removed[0].startswith("milky-frog-")
    assert removed[0] == _container_name_in(cli)


def _container_name_in(cli: StubDockerCli) -> str:
    run_argv = next(argv for argv in cli.captured if argv[:2] == ["docker", "run"])
    return run_argv[run_argv.index("--name") + 1]


async def test_concurrent_run_commands_start_exactly_one_container(tmp_path: Path) -> None:
    """acquire() holds the lock across _start. If it ever stops doing so, the
    second `docker run` overwrites the first id and that container leaks."""
    cli = SlowRunDockerCli(container_id="c", unique_ids=True)
    factory = _factory(cli)
    sandbox = factory(tmp_path)

    await asyncio.gather(
        sandbox.run_command("echo one", timeout_seconds=5),
        sandbox.run_command("echo two", timeout_seconds=5),
    )

    assert cli.run_count() == 1

    await factory.aclose()
    assert cli.removed_ids() == ["c-1"]
