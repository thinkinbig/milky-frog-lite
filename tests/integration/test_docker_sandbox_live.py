"""Live Docker tests. Skipped unless a real daemon answers `docker version`.

These are the only tests that touch a container; everything else stubs the
`DockerCli` seam. So each test here must assert something a stub *cannot* —
container reuse by effect, a container's absence from the daemon after
`aclose()`, wall-clock timeout behaviour. Anything provable against
`StubDockerCli` belongs in `tests/adapters/test_docker_sandbox.py`, where CI
actually runs it; a daemon-guarded assertion is one CI never executes.

The reachability guard shells out synchronously (``subprocess.run``) rather
than calling ``asyncio.run(docker_is_available())`` at import time: this
module is imported during collection, before pytest-asyncio has a running
event loop, so ``asyncio.run`` would likely work too — but relying on "no
loop happens to be running yet during collection" is fragile as the suite
grows, and a plain subprocess call sidesteps the question entirely while
checking the exact same thing (``docker version`` reaching the daemon, not
just the binary existing).
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.docker.sandbox import WORKSPACE_LABEL, _workspace_digest
from milky_frog.core.sandbox import CommandResult, CommandTimeout
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.builtins.bash import BashTool

# alpine has no bash; `run_command` uses `sh -c`, so BashTool works anyway.
IMAGE = "alpine:3.20"


def _docker(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", *args], capture_output=True, timeout=timeout, check=False)


@functools.cache
def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return _docker("version", "--format", "{{.Server.Version}}", timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")


@pytest.fixture(scope="session")
def image() -> str:
    """Ensure the image is local before any test runs.

    `capture()` has no timeout, so an implicit pull inside `docker run` on a
    cold cache would hang the suite rather than fail it. A missing image is an
    environment problem, not a defect — skip instead of erroring.
    """
    if _docker("image", "inspect", IMAGE, timeout=15).returncode == 0:
        return IMAGE
    try:
        pulled = _docker("pull", IMAGE, timeout=180)
    except subprocess.TimeoutExpired:
        pytest.skip(f"timed out pulling {IMAGE}")
    if pulled.returncode != 0:
        pytest.skip(f"cannot obtain {IMAGE}")
    return IMAGE


@pytest.fixture
async def factory(image: str) -> AsyncIterator[DockerSandboxFactory]:
    made = DockerSandboxFactory(image=image, workspace_mount="/mnt/workspace")
    try:
        yield made
    finally:
        await made.aclose()


def _container_ids(workspace: Path) -> list[str]:
    """Containers the daemon still holds for this Workspace, via the label."""
    result = _docker(
        "ps", "-aq", "--filter", f"label={WORKSPACE_LABEL}={_workspace_digest(workspace)}"
    )
    return result.stdout.decode().split()


async def test_bash_runs_in_container_with_workspace_cwd(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    (tmp_path / "note.txt").write_text("hello from host\n", encoding="utf-8")
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("pwd && cat note.txt", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 0
    assert "/mnt/workspace" in outcome.output
    assert "hello from host" in outcome.output


async def test_container_writes_are_visible_on_the_host(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """The bind mount is what lets read_file/grep keep working unchanged."""
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("echo written-inside > out.txt", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 0
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "written-inside"


async def test_the_same_container_is_reused_across_commands(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """Reuse proven by effect. The stub can only count `docker run` argv.

    /tmp is inside the container, not the bind mount, so the second command
    can only see this file if it ran in the same container.
    """
    sandbox = factory(tmp_path)

    await sandbox.run_command("echo marker > /tmp/probe", timeout_seconds=30)
    outcome = await sandbox.run_command("cat /tmp/probe", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 0
    assert "marker" in outcome.output


async def test_aclose_removes_the_container_from_the_daemon(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """The central claim of the design, checked against the daemon rather than
    against a list of argv the stub recorded."""
    sandbox = factory(tmp_path)
    await sandbox.run_command("true", timeout_seconds=30)

    assert len(_container_ids(tmp_path)) == 1

    await factory.aclose()

    assert _container_ids(tmp_path) == []


async def test_bash_tool_end_to_end_in_container(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    context = ToolContext("run-1", tmp_path, sandbox=factory(tmp_path))

    result = await BashTool().execute(context, BashTool.input_model(command="echo hi"))

    assert not result.is_error
    assert "hi" in result.content


async def test_bash_nonzero_exit_is_error(tmp_path: Path, factory: DockerSandboxFactory) -> None:
    context = ToolContext("run-1", tmp_path, sandbox=factory(tmp_path))

    result = await BashTool().execute(context, BashTool.input_model(command="exit 3"))

    assert result.is_error
    assert "exit code 3" in result.content


async def test_grep_over_the_bind_mount_sees_host_files(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """File Tools stay host-side; the mount keeps them consistent with bash."""
    (tmp_path / "app.py").write_text("def handler():\n    return 42\n", encoding="utf-8")
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("grep -n handler app.py", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert "def handler" in outcome.output


async def test_timeout_returns_promptly_and_leaves_the_container_usable(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """Only a daemon can answer this: the timeout kills the host-side `docker
    exec` client and leaves `sleep 30` running inside. The container must still
    serve the next command."""
    sandbox = factory(tmp_path)

    started = time.monotonic()
    outcome = await sandbox.run_command("sleep 30", timeout_seconds=2.0)
    elapsed = time.monotonic() - started

    assert isinstance(outcome, CommandTimeout)
    assert outcome.seconds == 2.0
    assert elapsed < 10, f"waited {elapsed:.1f}s — the timeout did not fire"

    alive = await sandbox.run_command("echo alive", timeout_seconds=30)
    assert isinstance(alive, CommandResult)
    assert "alive" in alive.output


async def test_host_env_does_not_reach_the_container(
    tmp_path: Path, factory: DockerSandboxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LocalSandbox forwards TMPDIR/SHELL; DockerSandbox must not.

    Sentinels go in variables LocalSandbox *does* forward, so swapping
    DockerSandbox.build_env for LocalSandbox.build_env would fail this test.
    The positive half proves the `-e` flags reach the container at all —
    without it, every negative assertion here would hold vacuously.
    """
    monkeypatch.setenv("TMPDIR", "/host/sentinel-tmpdir")
    monkeypatch.setenv("SHELL", "/host/sentinel-shell")
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("env", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert "sentinel-tmpdir" not in outcome.output
    assert "sentinel-shell" not in outcome.output
    assert "CI=true" in outcome.output
    assert "GIT_TERMINAL_PROMPT=0" in outcome.output


async def test_masked_paths_are_empty_in_the_container_and_intact_on_the_host(
    tmp_path: Path, image: str
) -> None:
    """The bind mount would carry a host-built .venv into the container, where
    its macOS interpreter is a broken symlink and its native modules will not
    load. A model that finds no toolchain reaches for it anyway and reports a
    misleading failure. Masked, it is plainly empty; the host copy is untouched.
    """
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /host/python\n", encoding="utf-8")

    factory = DockerSandboxFactory(
        image=image, workspace_mount="/mnt/workspace", mask_paths=(".venv",)
    )
    try:
        outcome = await factory(tmp_path).run_command("ls -A .venv | wc -l", timeout_seconds=30)
    finally:
        await factory.aclose()

    assert isinstance(outcome, CommandResult)
    assert outcome.output.strip() == "0", "masked .venv should be empty in the container"
    assert (venv / "pyvenv.cfg").exists(), "the host's .venv must not be touched"


async def test_unmasked_workspace_still_exposes_host_dirs(tmp_path: Path, image: str) -> None:
    """The other half: without masking the host dir really is visible, so the
    test above is measuring the mask and not some unrelated emptiness."""
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /host/python\n", encoding="utf-8")

    factory = DockerSandboxFactory(image=image, workspace_mount="/mnt/workspace")
    try:
        outcome = await factory(tmp_path).run_command("ls -A .venv", timeout_seconds=30)
    finally:
        await factory.aclose()

    assert isinstance(outcome, CommandResult)
    assert "pyvenv.cfg" in outcome.output
