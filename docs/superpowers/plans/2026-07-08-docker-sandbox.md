# Container Sandbox (DockerSandbox) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in containerized `Sandbox` adapter so `bash` and post-edit verification commands execute inside a Docker container against a bind-mounted workspace, without changing any Tool or the Harness.

**Architecture:** First extend the existing `Sandbox` protocol (`core/sandbox.py`) with `async run_command() -> CommandOutcome` and move the duplicated subprocess logic out of `BashTool` and `VerificationHandler` into it (porting the unmerged draft from commit `4d47a88`). Then add `DockerSandbox`, which composes a `LocalSandbox` for path/deny policy (workspace is bind-mounted, so host-side file I/O still works and no file Tool changes) and implements `run_command()` by shelling out to `docker exec` against a lazily-created, reused container.

**Tech Stack:** Python 3.12+, asyncio subprocess (`docker` CLI — no new dependency), pydantic v2 (config models), pytest (`asyncio_mode=auto`), ruff, pyrefly (strict).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-08-docker-sandbox-design.md`. Issue: [#60](https://github.com/thinkinbig/milky-frog-lite/issues/60).
- `from __future__ import annotations` at the top of every new module.
- ruff line length 100; rules E, F, I, UP, B, SIM, RUF.
- pyrefly **strict** preset must pass.
- **No bare `lambda`** for callbacks or sort keys in production code. Seams are `typing.Protocol` or small named classes. Test doubles are named classes in `tests/stubs.py`.
- Frozen `@dataclass(frozen=True, slots=True)` for domain value types; pydantic `BaseModel` for config bodies.
- Domain language (`CONTEXT.md`) is enforced. Use **Sandbox**, **Tool**, **Handler**, **Workspace**, **Run**. Never use "execution backend", "middleware", "session" (for Run), "plugin".
- `workspace_mount` must live under `/mnt`; default `/mnt/workspace`.
- Docker is **opt-in**. `LocalSandbox` stays the default everywhere.
- No new required dependency. Docker is reached via the `docker` CLI on `PATH`.
- Run all four before considering any task done: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyrefly check`.
- pytest runs with `--cov-fail-under=80`. To run a single test without tripping coverage, add `--no-cov`.

## File Structure

**Create:**
- `src/milky_frog/adapters/process.py` — shared post-processing for a finished command: decode bytes, normalize CR/CRLF, strip ANSI, build `CommandResult`; plus the terminal-presentation env enrichment. Used by both the local and docker adapters so output semantics are identical.
- `src/milky_frog/adapters/local/command.py` — `run_local_command()`: host `asyncio.create_subprocess_shell` + timeout-kill.
- `src/milky_frog/adapters/docker/__init__.py` — exports `DockerSandbox`, `DockerSandboxFactory`.
- `src/milky_frog/adapters/docker/cli.py` — `DockerCli` Protocol + `SubprocessDockerCli` (the only place that shells out to `docker`). This is the seam the unit tests stub, so no test needs a Docker daemon.
- `src/milky_frog/adapters/docker/sandbox.py` — `DockerSandbox`, `ContainerRegistry`, `DockerSandboxFactory`.
- `tests/adapters/__init__.py`, `tests/adapters/test_docker_sandbox.py` — unit tests against a stubbed `DockerCli`.
- `tests/integration/__init__.py`, `tests/integration/test_docker_sandbox_live.py` — skipped unless a real Docker daemon is reachable.

**Modify:**
- `src/milky_frog/core/sandbox.py` — add `CommandPresentation`, `CommandResult`, `CommandTimeout`, `CommandStartError`, `CommandOutcome`, and `Sandbox.run_command()`.
- `src/milky_frog/adapters/local/sandbox.py` — `LocalSandbox.run_command()` delegating to `run_local_command()`.
- `src/milky_frog/adapters/local/__init__.py` — export `run_local_command`.
- `src/milky_frog/harness/tools/builtins/bash.py` — delete inline subprocess logic; call `sandbox.run_command()`.
- `src/milky_frog/handlers/verification.py` — call `sandbox.run_command()` per configured command.
- `src/milky_frog/project.py` — `SandboxConfig`, `ProjectConfig.sandbox`, `SandboxConfigError`, `validate_sandbox_config()`, `CONFIG_TEMPLATE` comment block.
- `src/milky_frog/core/runtime/assemble.py` — `make_sandbox_factory(config)`.
- `src/milky_frog/app/session.py` — build the factory from project config; close it on exit.
- `src/milky_frog/core/shutdown.py` — optional `sandbox_factory` with `aclose()`.
- `src/milky_frog/cli/actions.py` — Docker diagnostic in `build_doctor_diagnostics`.
- `src/milky_frog/cli/commands.py`, `src/milky_frog/cli/launch.py` — surface `SandboxConfigError` at startup.
- `tests/stubs.py` — `StubDockerCli`.
- `tests/harness/test_sandbox.py`, `tests/harness/test_builtin_tools.py`, `tests/handlers/test_verification.py` — retarget to the seam.
- `docs/ARCHITECTURE.md`, `CONTEXT.md`, `README.md`.

---

## Task 1: `run_command()` on the Sandbox seam

Extend the protocol and implement it for `LocalSandbox` by extracting the logic that lives inline in `bash.py` today. `bash.py` is **not** touched in this task — it keeps working unchanged, so this task is independently reviewable and its tests prove the extraction is faithful before anything depends on it.

**Files:**
- Modify: `src/milky_frog/core/sandbox.py`
- Create: `src/milky_frog/adapters/process.py`
- Create: `src/milky_frog/adapters/local/command.py`
- Modify: `src/milky_frog/adapters/local/sandbox.py`
- Modify: `src/milky_frog/adapters/local/__init__.py`
- Test: `tests/harness/test_sandbox.py`

**Interfaces:**
- Consumes: `LocalSandbox.workspace`, `LocalSandbox.build_env()`, `LocalSandbox.config` (all exist today).
- Produces:
  - `CommandPresentation.PLAIN | CommandPresentation.TERMINAL` (`StrEnum`)
  - `CommandResult(exit_code: int, output: str, display_output: str | None = None)`
  - `CommandTimeout(seconds: float)`
  - `CommandStartError(message: str)`
  - `CommandOutcome = CommandResult | CommandTimeout | CommandStartError`
  - `Sandbox.run_command(command: str, *, timeout_seconds: float, presentation: CommandPresentation = CommandPresentation.PLAIN) -> CommandOutcome`
  - `milky_frog.adapters.process.make_command_result(exit_code: int, raw: bytes) -> CommandResult`
  - `milky_frog.adapters.process.with_presentation_env(env: dict[str, str]) -> dict[str, str]`
  - `milky_frog.adapters.local.command.run_local_command(command: str, *, workspace: Path, env: dict[str, str], timeout_seconds: float, presentation: CommandPresentation) -> CommandOutcome`

- [ ] **Step 1: Write the failing tests**

Append to `tests/harness/test_sandbox.py`:

```python
async def test_sandbox_run_command_uses_workspace_as_cwd(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("workspace file", encoding="utf-8")
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command("cat note.txt", timeout_seconds=5)

    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 0
    assert outcome.output == "workspace file"


async def test_sandbox_run_command_merges_stderr_into_output(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command("echo 'err msg' >&2 && false", timeout_seconds=5)

    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 1
    assert "err msg" in outcome.output


async def test_sandbox_run_command_terminal_presentation_keeps_display_output(
    tmp_path: Path,
) -> None:
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command(
        "printf '\\033[31mred\\033[0m\\n'",
        timeout_seconds=5,
        presentation=CommandPresentation.TERMINAL,
    )

    assert isinstance(outcome, CommandResult)
    assert outcome.output == "red\n"
    assert outcome.display_output is not None
    assert "\x1b[31mred" in outcome.display_output


async def test_sandbox_run_command_plain_presentation_has_no_display_output(
    tmp_path: Path,
) -> None:
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command("echo hi", timeout_seconds=5)

    assert isinstance(outcome, CommandResult)
    assert outcome.display_output is None


async def test_sandbox_run_command_timeout(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command(
        "python3 -c 'import time; time.sleep(5)'",
        timeout_seconds=1,
    )

    assert isinstance(outcome, CommandTimeout)
    assert outcome.seconds == 1


async def test_sandbox_run_command_start_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_exec(*args: object, **kwargs: object) -> object:
        raise OSError("command not found")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", failing_exec)
    sandbox = LocalSandbox(tmp_path)

    outcome = await sandbox.run_command("echo hi", timeout_seconds=5)

    assert isinstance(outcome, CommandStartError)
    assert "command not found" in outcome.message
```

Update that file's imports to:

```python
import asyncio
from pathlib import Path

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandStartError,
    CommandTimeout,
    SandboxViolation,
)
from milky_frog.project import ProjectConfig
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/harness/test_sandbox.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'CommandPresentation' from 'milky_frog.core.sandbox'`

- [ ] **Step 3: Add the types and protocol method**

Replace `src/milky_frog/core/sandbox.py` with:

```python
"""Sandbox protocol — core seam for Workspace execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from milky_frog.project import ProjectConfig


class SandboxViolation(PermissionError):
    """Raised when a path resolution violates the Sandbox policy."""


class CommandPresentation(StrEnum):
    """How much terminal presentation a command runner should request."""

    PLAIN = "plain"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Completed command output captured under Sandbox policy."""

    exit_code: int
    output: str
    display_output: str | None = None


@dataclass(frozen=True, slots=True)
class CommandTimeout:
    """Command exceeded its configured timeout."""

    seconds: float


@dataclass(frozen=True, slots=True)
class CommandStartError:
    """Command could not be started by the Sandbox adapter."""

    message: str


CommandOutcome = CommandResult | CommandTimeout | CommandStartError


class Sandbox(Protocol):
    """Policy boundary for Workspace execution."""

    workspace: Path
    config: ProjectConfig

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...

    def build_env(self) -> dict[str, str]: ...

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome: ...


class SandboxFactory(Protocol):
    """Create a Sandbox for a given Workspace."""

    def __call__(self, workspace: Path) -> Sandbox: ...
```

- [ ] **Step 4: Add the shared post-processing module**

Create `src/milky_frog/adapters/process.py`:

```python
"""Post-processing shared by every Sandbox command runner.

Both ``LocalSandbox`` (host subprocess) and ``DockerSandbox`` (``docker exec``)
capture raw bytes with stderr merged into stdout, then turn them into the same
``CommandResult`` shape. Keeping the decode / newline-normalization / ANSI-strip
rules here means the two adapters cannot drift in what a Tool observes.
"""

from __future__ import annotations

import re

from milky_frog.core.sandbox import CommandResult

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")

_PRESENTATION_ENV: dict[str, str] = {
    "COLORTERM": "truecolor",
    "CLICOLOR_FORCE": "1",
    "FORCE_COLOR": "1",
}

_GIT_COLOR_ENV: dict[str, str] = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "color.ui",
    "GIT_CONFIG_VALUE_0": "always",
}


def with_presentation_env(env: dict[str, str]) -> dict[str, str]:
    """Enrich *env* so child processes emit colour a Terminal UI can render."""
    enriched = {**env, **_PRESENTATION_ENV}
    enriched.setdefault("TERM", "xterm-256color")
    if "GIT_CONFIG_COUNT" not in enriched:
        enriched.update(_GIT_COLOR_ENV)
    return enriched


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def make_command_result(exit_code: int, raw: bytes) -> CommandResult:
    """Decode captured bytes into the model-facing and display-facing texts.

    ``output`` is ANSI-stripped (what the model reads); ``display_output`` keeps
    the escape codes and is ``None`` when stripping changed nothing.
    """
    display_text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    text = strip_ansi(display_text)
    display = display_text if display_text != text else None
    return CommandResult(exit_code=exit_code, output=text, display_output=display)
```

- [ ] **Step 5: Add the local command runner**

Create `src/milky_frog/adapters/local/command.py`:

```python
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from milky_frog.adapters.process import make_command_result, with_presentation_env
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    CommandStartError,
    CommandTimeout,
)


def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return
    process.kill()


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process, timeout_seconds: float
) -> bytes:
    communicate_task = asyncio.create_task(process.communicate())
    try:
        stdout, _stderr = await asyncio.wait_for(
            asyncio.shield(communicate_task), timeout=timeout_seconds
        )
    except TimeoutError:
        _kill_process(process)
        await communicate_task
        raise
    except BaseException:
        _kill_process(process)
        raise
    return stdout if stdout is not None else b""


async def run_local_command(
    command: str,
    *,
    workspace: Path,
    env: dict[str, str],
    timeout_seconds: float,
    presentation: CommandPresentation = CommandPresentation.PLAIN,
) -> CommandOutcome:
    """Run *command* on the host, capturing stdout with stderr merged in.

    Stdin is closed so interactive prompts cannot hang the Run. On POSIX the
    child gets its own process group so a timeout kills the whole tree.
    """
    if presentation is CommandPresentation.TERMINAL:
        env = with_presentation_env(env)

    try:
        if os.name == "posix":
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
                env=env,
                start_new_session=True,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
                env=env,
            )
    except OSError as error:
        return CommandStartError(str(error))

    try:
        raw = await _communicate_with_timeout(process, timeout_seconds)
    except TimeoutError:
        return CommandTimeout(timeout_seconds)

    return make_command_result(
        process.returncode if process.returncode is not None else 0, raw
    )
```

- [ ] **Step 6: Implement `LocalSandbox.run_command`**

In `src/milky_frog/adapters/local/sandbox.py`, change the import line and append the method.

Replace:
```python
from milky_frog.core.sandbox import SandboxViolation
```
with:
```python
from milky_frog.adapters.local.command import run_local_command
from milky_frog.core.sandbox import CommandOutcome, CommandPresentation, SandboxViolation
```

Append to the `LocalSandbox` class (after `build_env`):
```python
    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        return await run_local_command(
            command,
            workspace=self.workspace,
            env=self.build_env(),
            timeout_seconds=timeout_seconds,
            presentation=presentation,
        )
```

Replace `src/milky_frog/adapters/local/__init__.py` with:
```python
from milky_frog.adapters.local.command import run_local_command
from milky_frog.adapters.local.sandbox import LocalSandbox

__all__ = ["LocalSandbox", "run_local_command"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/harness/test_sandbox.py -v --no-cov`
Expected: PASS (all tests, including the six new `run_command` tests)

- [ ] **Step 8: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass. `bash.py` is untouched, so its existing tests still pass against its own inline implementation.

- [ ] **Step 9: Commit**

```bash
git add src/milky_frog/core/sandbox.py src/milky_frog/adapters/process.py \
        src/milky_frog/adapters/local/ tests/harness/test_sandbox.py
git commit -m "feat: add run_command() to the Sandbox seam"
```

---

## Task 2: Route `BashTool` through `Sandbox.run_command()`

Delete the duplicated subprocess logic from the Tool. Behavior must not change — the existing `test_builtin_tools.py` bash tests are the regression net.

**Files:**
- Modify: `src/milky_frog/harness/tools/builtins/bash.py`
- Test: `tests/harness/test_builtin_tools.py:440-451` (one test must be rewritten)

**Interfaces:**
- Consumes: `Sandbox.run_command()`, `CommandResult`, `CommandTimeout`, `CommandStartError`, `CommandPresentation` from Task 1.
- Produces: nothing new. `BashTool.execute()` keeps its signature.

- [ ] **Step 1: Rewrite the one test that reaches past the seam**

`test_bash_os_error` currently monkeypatches `asyncio.create_subprocess_shell`, which `bash.py` will no longer import. Replace it (at `tests/harness/test_builtin_tools.py:440`) with a test that stubs the sandbox's `run_command` instead.

First add to `tests/stubs.py` (append at end of file):

```python
class FixedOutcomeSandbox:
    """Sandbox wrapper that returns a canned CommandOutcome from run_command.

    Lets a Tool test exercise every ``CommandOutcome`` branch without spawning
    a real process. Path resolution and config still come from a real
    ``LocalSandbox`` so deny-policy behaviour is unchanged.
    """

    def __init__(self, workspace: Path, outcome: CommandOutcome) -> None:
        self._inner = LocalSandbox(workspace)
        self._outcome = outcome
        self.workspace = self._inner.workspace
        self.config = self._inner.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        return self._inner.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        return self._inner.build_env()

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        return self._outcome
```

and extend `tests/stubs.py` imports:
```python
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    Sandbox,
)
```
(replacing the existing `from milky_frog.core.sandbox import Sandbox`)

Then in `tests/harness/test_builtin_tools.py`, replace `test_bash_os_error` with:

```python
async def test_bash_start_error(tmp_path: Path) -> None:
    context = ToolContext(
        "run-1",
        tmp_path,
        sandbox=FixedOutcomeSandbox(tmp_path, CommandStartError("command not found")),
    )

    result = await BashTool().execute(context, BashTool.input_model(command="echo hi"))

    assert result.is_error
    assert "failed to run command" in result.content
    assert "command not found" in result.content
```

Add the imports it needs to that test file:
```python
from milky_frog.core.sandbox import CommandStartError
from stubs import FixedOutcomeSandbox
```
Remove the now-unused `import asyncio` from `tests/harness/test_builtin_tools.py` **only if** no other test in the file still uses it — check with `grep -n "asyncio\." tests/harness/test_builtin_tools.py` first.

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `uv run pytest tests/harness/test_builtin_tools.py::test_bash_start_error -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'FixedOutcomeSandbox'` before you add the stub, then FAIL on the assertion (the tool still calls the real subprocess and returns success) once the stub exists but `bash.py` is unchanged.

- [ ] **Step 3: Rewrite `bash.py` against the seam**

Replace the whole of `src/milky_frog/harness/tools/builtins/bash.py` with:

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandStartError,
    CommandTimeout,
)
from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output
from milky_frog.project import DEFAULT_BASH_OUTPUT_MAX_CHARS, DEFAULT_BASH_TIMEOUT_SECONDS


class BashInput(BaseModel):
    command: str = Field(description="Shell command to run in the workspace directory.")


class BashTool:
    """Run a shell command inside the Workspace directory and capture output.

    Execution is delegated to the injected ``Sandbox`` — locally that is a host
    subprocess, under the container Sandbox it is ``docker exec``. The command
    runs non-interactively with stdout/stderr captured together and stdin closed,
    so git log and similar commands cannot hang waiting for a human. Oversized
    output is passed to ``truncate_tool_output`` (``bash_output_max_chars`` in
    ``.milky-frog/config.toml``). Timeout is configurable via
    ``bash_timeout_seconds`` in the same file.
    """

    name = "bash"
    requires_approval = True
    description = (
        "Run a shell command in the workspace and capture its stdout and stderr. "
        "Large output is truncated via head/tail with the full text spilled to disk "
        f"(default inline cap {DEFAULT_BASH_OUTPUT_MAX_CHARS} chars; "
        "override bash_output_max_chars in .milky-frog/config.toml). "
        "Commands run non-interactively (no pagers or terminal prompts; stdin closed). "
        "Host env is limited to HOME, PATH, SHELL, TERM, LANG, LC_ALL, TMPDIR. "
        f"Default timeout is {DEFAULT_BASH_TIMEOUT_SECONDS} seconds; "
        "override with bash_timeout_seconds in .milky-frog/config.toml."
    )
    input_model: type[BaseModel] = BashInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = BashInput.model_validate(input)
        command = params.command.strip()
        if not command:
            return ToolResult("empty command", is_error=True)

        sandbox = context.require_sandbox()
        outcome = await sandbox.run_command(
            command,
            timeout_seconds=float(sandbox.config.bash_timeout_seconds),
            presentation=CommandPresentation.TERMINAL,
        )

        match outcome:
            case CommandStartError(message=message):
                return ToolResult(f"failed to run command: {message}", is_error=True)
            case CommandTimeout(seconds=seconds):
                return ToolResult(f"command timed out after {seconds:g}s", is_error=True)
            case CommandResult():
                pass

        max_chars = sandbox.config.bash_output_max_chars
        text = outcome.output
        display_content = outcome.display_output
        if len(text) > max_chars:
            display_content = None

        if outcome.exit_code != 0:
            text = truncate_tool_output(
                text,
                max_chars=max_chars,
                workspace=sandbox.workspace,
                label="bash",
                counter=context.token_counter,
            )
            stripped = text.strip() or "(no output)"
            display_result = (
                f"exit code {outcome.exit_code}:\n{display_content.strip() or '(no output)'}"
                if display_content is not None
                else None
            )
            return ToolResult(
                f"exit code {outcome.exit_code}:\n{stripped}",
                is_error=True,
                display_content=display_result,
            )

        text = truncate_tool_output(
            text,
            max_chars=max_chars,
            workspace=sandbox.workspace,
            label="bash",
            counter=context.token_counter,
        )
        result = text.rstrip("\n")
        display_result = display_content.rstrip("\n") if display_content is not None else None
        return ToolResult(result if result else "(no output)", display_content=display_result)
```

Note the `match` narrows `outcome` to `CommandResult` for the code below it. If pyrefly complains the match isn't exhaustive, add `case _: return ToolResult("unknown command outcome", is_error=True)`.

- [ ] **Step 4: Run the bash tests**

Run: `uv run pytest tests/harness/test_builtin_tools.py -v --no-cov -k bash`
Expected: PASS — every pre-existing bash test (echo, exit code, stderr merge, ANSI, timeout, truncation, no-pager) still passes unchanged, plus the new `test_bash_start_error`.

- [ ] **Step 5: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/milky_frog/harness/tools/builtins/bash.py tests/harness/test_builtin_tools.py tests/stubs.py
git commit -m "refactor: run bash through Sandbox.run_command()"
```

---

## Task 3: Route `VerificationHandler` through `Sandbox.run_command()`

Today it builds its own `asyncio.create_subprocess_shell` — the same host-escape gap `bash` just closed. Under the Docker Sandbox this would silently run `uv run pytest` on the host while `bash` ran in the container.

**Files:**
- Modify: `src/milky_frog/handlers/verification.py`
- Test: `tests/handlers/test_verification.py`

**Interfaces:**
- Consumes: `Sandbox.run_command()`, `CommandResult`, `CommandTimeout`, `CommandStartError` (Task 1).
- Produces: nothing new. `VerificationHandler(sandbox_factory)` keeps its constructor signature.

Behavioral note: verification commands use `CommandPresentation.PLAIN` (their output is injected into the transcript for the model to read, not rendered as terminal colour) and reuse `bash_timeout_seconds` as their per-command timeout. A timeout or start error marks the run as failed rather than raising — failures never block the loop, per the handler's existing docstring.

- [ ] **Step 1: Write the failing tests**

Append to `tests/handlers/test_verification.py`:

```python
async def test_verification_uses_sandbox_run_command(tmp_path: Path) -> None:
    """Commands go through the Sandbox seam, not a raw host subprocess."""
    _write_config(tmp_path, '[verification]\nafter_edit = true\ncommands = ["echo hello"]\n')
    recorder = RecordingCommandSandboxFactory()
    handler = VerificationHandler(recorder)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    call = ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"})
    results = await hub.after_tool("test", call, ToolResult("ok", is_error=False), state)

    notices = [r for r in results if isinstance(r, VerificationNotice)]
    assert recorder.commands == ["echo hello"]
    assert len(notices) == 1
    assert notices[0].exit_code_summary == "all pass"


async def test_verification_reports_timeout_as_failure(tmp_path: Path) -> None:
    _write_config(tmp_path, '[verification]\nafter_edit = true\ncommands = ["sleep 99"]\n')
    handler = VerificationHandler(TimingOutSandboxFactory())
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    call = ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"})
    results = await hub.after_tool("test", call, ToolResult("ok", is_error=False), state)

    notices = [r for r in results if isinstance(r, VerificationNotice)]
    assert len(notices) == 1
    assert notices[0].exit_code_summary == "one or more commands FAILED"
    assert "timed out" in notices[0].summary
```

This mirrors the calling convention the existing tests in that file already use
(`await hub.after_tool("test", call, result, state)` returning a list of handler
results, filtered for `VerificationNotice`) — do not call `handler._on_after_tool`
directly.

Add to `tests/stubs.py`:

```python
class RecordingCommandSandbox:
    """Sandbox that records commands and reports success without running them."""

    def __init__(self, workspace: Path, recorder: list[str]) -> None:
        self._inner = LocalSandbox(workspace)
        self._recorder = recorder
        self.workspace = self._inner.workspace
        self.config = self._inner.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        return self._inner.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        return self._inner.build_env()

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        self._recorder.append(command)
        return CommandResult(exit_code=0, output=f"ran {command}")


class RecordingCommandSandboxFactory:
    """SandboxFactory yielding RecordingCommandSandbox, sharing one command log."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def __call__(self, workspace: Path) -> Sandbox:
        return RecordingCommandSandbox(workspace, self.commands)


class TimingOutSandboxFactory:
    """SandboxFactory whose sandboxes always report a CommandTimeout."""

    def __call__(self, workspace: Path) -> Sandbox:
        return FixedOutcomeSandbox(workspace, CommandTimeout(seconds=1.0))
```

and extend the `tests/stubs.py` sandbox import to:
```python
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    CommandResult,
    CommandTimeout,
    Sandbox,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/handlers/test_verification.py -v --no-cov`
Expected: FAIL — `test_verification_uses_sandbox_run_command` fails with `AssertionError` on `recorder.commands == ["echo hello"]` (empty list: the handler still spawns its own subprocess).

- [ ] **Step 3: Rewrite `verification.py`**

Replace `src/milky_frog/handlers/verification.py` with:

```python
from __future__ import annotations

from typing import override

from milky_frog.core.handlers import HandlerDeps
from milky_frog.core.sandbox import (
    CommandResult,
    CommandStartError,
    CommandTimeout,
    SandboxFactory,
)
from milky_frog.domain import VerificationNotice
from milky_frog.events.events import RunAfterTool
from milky_frog.events.hub import EventHub, Handler
from milky_frog.project import load_project_config

_TRIGGER_TOOLS = frozenset({"edit_file", "write_file"})


class VerificationHandler(Handler):
    """Runs configured verification commands after every successful edit Tool.

    Subscribes to ``RunAfterTool``. When ``call.name`` is ``edit_file`` or
    ``write_file`` and the tool succeeded (``is_error is False``), runs the
    per-workspace ``[verification].commands`` sequentially through the Sandbox
    seam — so they execute wherever the Tools do — and returns a
    ``VerificationNotice``. The loop injects it as a synthetic tool result.

    Commands that time out or fail to start are reported as failures rather
    than raised: verification never blocks the loop.

    When ``after_edit`` is disabled in the workspace config, this is a no-op.
    """

    def __init__(self, sandbox_factory: SandboxFactory) -> None:
        self._sandbox_factory = sandbox_factory

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunAfterTool)(self._on_after_tool)

    async def _on_after_tool(
        self, event: RunAfterTool, deps: HandlerDeps
    ) -> VerificationNotice | None:
        if event.call.name not in _TRIGGER_TOOLS:
            return None
        if event.result.is_error:
            return None

        config = load_project_config(event.state.workspace)
        if not config.verification.after_edit:
            return None

        sandbox = self._sandbox_factory(event.state.workspace)
        timeout_seconds = float(config.bash_timeout_seconds)

        outputs: list[str] = []
        all_passed = True

        for cmd in config.verification.commands:
            outcome = await sandbox.run_command(cmd, timeout_seconds=timeout_seconds)
            match outcome:
                case CommandResult(exit_code=exit_code, output=output):
                    body = output.rstrip()
                    if exit_code != 0:
                        all_passed = False
                case CommandTimeout(seconds=seconds):
                    body = f"command timed out after {seconds:g}s"
                    all_passed = False
                case CommandStartError(message=message):
                    body = f"failed to run command: {message}"
                    all_passed = False

            parts = [f"$ {cmd}"]
            if body:
                parts.append(body)
            outputs.append("\n".join(parts))

        return VerificationNotice(
            summary="\n\n".join(outputs),
            exit_code_summary="all pass" if all_passed else "one or more commands FAILED",
        )
```

Note: this drops the separate stdout/stderr split the old code had — `run_command` merges stderr into stdout, matching what `bash` already shows the model.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/handlers/test_verification.py -v --no-cov`
Expected: PASS — including the pre-existing tests. If a pre-existing test asserts on separate stderr text, update its expected string to the merged form; do not reintroduce the split.

- [ ] **Step 5: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/milky_frog/handlers/verification.py tests/handlers/test_verification.py tests/stubs.py
git commit -m "refactor: run verification commands through Sandbox.run_command()"
```

---

## Task 4: `[sandbox]` config schema with loud failure

**Files:**
- Modify: `src/milky_frog/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Produces:
  - `SandboxConfig(kind: Literal["local","docker"] = "local", image: str | None = None, workspace_mount: str = "/mnt/workspace")`
  - `ProjectConfig.sandbox: SandboxConfig`
  - `SandboxConfigError(ValueError)`
  - `validate_sandbox_config(workspace: Path) -> None` — raises `SandboxConfigError` on a bad `[sandbox]` table
  - `DEFAULT_WORKSPACE_MOUNT = "/mnt/workspace"`

Design note: `load_project_config()` stays lenient (it is called on hot paths — `harness/budget.py`, `handlers/verification.py`, `core/runtime/foreground.py` — where raising mid-Run would be wrong). The loud failure lives in `validate_sandbox_config()`, called once at CLI startup and by `doctor` (Task 7). A bad `[sandbox]` table therefore stops the process before a Run begins, instead of silently downgrading to an unsandboxed `LocalSandbox`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project.py`:

```python
def test_sandbox_config_defaults_to_local(tmp_path: Path) -> None:
    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "local"
    assert config.sandbox.image is None
    assert config.sandbox.workspace_mount == "/mnt/workspace"


def test_sandbox_config_reads_docker_table(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[sandbox]\nkind = "docker"\nimage = "python:3.12-bookworm"\n',
    )

    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "docker"
    assert config.sandbox.image == "python:3.12-bookworm"
    assert config.sandbox.workspace_mount == "/mnt/workspace"


def test_sandbox_config_rejects_mount_outside_mnt() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(kind="local", workspace_mount="/workspace")


def test_sandbox_config_requires_image_for_docker() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(kind="docker")


def test_validate_sandbox_config_raises_on_docker_without_image(tmp_path: Path) -> None:
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')

    with pytest.raises(SandboxConfigError, match="image"):
        validate_sandbox_config(tmp_path)


def test_validate_sandbox_config_raises_on_bad_mount(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[sandbox]\nkind = "docker"\nimage = "python:3.12"\nworkspace_mount = "/workspace"\n',
    )

    with pytest.raises(SandboxConfigError, match="/mnt"):
        validate_sandbox_config(tmp_path)


def test_validate_sandbox_config_passes_when_absent(tmp_path: Path) -> None:
    validate_sandbox_config(tmp_path)  # no [sandbox] table: nothing to validate


def test_load_project_config_stays_lenient_on_bad_sandbox_table(tmp_path: Path) -> None:
    """The hot path never raises; validate_sandbox_config() is the loud gate."""
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')

    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "local"
```

Check the top of `tests/test_project.py` for an existing `_write_config` helper; if there isn't one, add:
```python
def _write_config(workspace: Path, body: str) -> None:
    root = workspace / PROJECT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILENAME).write_text(body, encoding="utf-8")
```
and import `PROJECT_DIRNAME`, `CONFIG_FILENAME`, `SandboxConfig`, `SandboxConfigError`, `validate_sandbox_config`, `load_project_config` from `milky_frog.project`, plus `pytest` and `from pydantic import ValidationError`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_project.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'SandboxConfig' from 'milky_frog.project'`

- [ ] **Step 3: Implement the config model**

In `src/milky_frog/project.py`:

Add to the imports at the top:
```python
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
```
(extend the existing pydantic import; add `Literal` to the typing import, adding the line if absent)

Add the constant next to the other `DEFAULT_*` values:
```python
DEFAULT_WORKSPACE_MOUNT = "/mnt/workspace"
```

Add above `class ProjectConfig`:
```python
class SandboxConfigError(ValueError):
    """Raised when the ``[sandbox]`` table is present but invalid.

    Unlike the rest of ``config.toml`` — where a malformed value silently
    yields defaults so a broken file never blocks a Run — a broken
    ``[sandbox]`` table must fail loudly. Silently falling back to
    ``LocalSandbox`` would leave a user who asked for container isolation
    running unsandboxed on the host with no signal.
    """


class SandboxConfig(BaseModel):
    """Which Sandbox adapter a Workspace uses, and how to build it.

    ``local`` (the default) runs Tools on the host under the path-deny policy.
    ``docker`` bind-mounts the Workspace into a container and runs commands
    there; it requires an ``image``.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["local", "docker"] = "local"
    image: str | None = None
    workspace_mount: str = DEFAULT_WORKSPACE_MOUNT

    @field_validator("workspace_mount")
    @classmethod
    def _require_mnt_prefix(cls, v: str) -> str:
        if not v.startswith("/mnt"):
            raise ValueError(f"workspace_mount must live under /mnt, got {v!r}")
        return v

    @model_validator(mode="after")
    def _require_image_for_docker(self) -> SandboxConfig:
        if self.kind == "docker" and not self.image:
            raise ValueError("image is required when sandbox.kind = 'docker'")
        return self
```

Add the field to `ProjectConfig`, next to `checkpoint` / `verification`:
```python
    sandbox: SandboxConfig = SandboxConfig()
```

Add at the end of the module:
```python
def _read_config_data(workspace: Path) -> dict[str, object] | None:
    path = project_root(workspace) / CONFIG_FILENAME
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def validate_sandbox_config(workspace: Path) -> None:
    """Raise ``SandboxConfigError`` if the ``[sandbox]`` table is invalid.

    Called once at startup (CLI entry, ``doctor``). ``load_project_config`` is
    deliberately left lenient because it runs on per-step hot paths where
    raising would abort a Run mid-flight.
    """
    data = _read_config_data(workspace)
    if data is None or "sandbox" not in data:
        return
    try:
        SandboxConfig.model_validate(data["sandbox"])
    except ValidationError as error:
        raise SandboxConfigError(f"invalid [sandbox] in {CONFIG_FILENAME}: {error}") from error
```

Refactor `load_project_config` to reuse `_read_config_data`:
```python
def load_project_config(workspace: Path) -> ProjectConfig:
    """Read ``<workspace>/.milky-frog/config.toml``; fall back to defaults.

    A missing or malformed file yields defaults rather than raising, so a
    broken config never blocks a Run. Callers that must not silently accept a
    broken ``[sandbox]`` table call ``validate_sandbox_config`` first.
    """
    data = _read_config_data(workspace)
    if data is None:
        return ProjectConfig()
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError:
        return ProjectConfig()
```

Append to `CONFIG_TEMPLATE`, before the closing `)`:
```python
    f"\n"
    f"# Execution Sandbox. 'local' runs Tools on the host under the path-deny\n"
    f"# policy. 'docker' bind-mounts the workspace into a container and runs\n"
    f"# bash + verification commands there (requires the docker CLI on PATH).\n"
    f"# [sandbox]\n"
    f'# kind = "docker"\n'
    f'# image = "python:3.12-bookworm"\n'
    f'# workspace_mount = "{DEFAULT_WORKSPACE_MOUNT}"  # must live under /mnt\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_project.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/milky_frog/project.py tests/test_project.py
git commit -m "feat: add [sandbox] config table with strict validation"
```

---

## Task 5: `DockerSandbox` adapter

The only Docker-specific code. Everything shells out through a `DockerCli` seam so unit tests need no daemon.

**Files:**
- Create: `src/milky_frog/adapters/docker/__init__.py`
- Create: `src/milky_frog/adapters/docker/cli.py`
- Create: `src/milky_frog/adapters/docker/sandbox.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/test_docker_sandbox.py`
- Modify: `tests/stubs.py`

**Interfaces:**
- Consumes: `LocalSandbox` (composed for path/deny policy), `CommandOutcome`/`CommandResult`/`CommandTimeout`/`CommandStartError`/`CommandPresentation` (Task 1), `make_command_result` / `with_presentation_env` (Task 1), `ProjectConfig` / `load_project_config`.
- Produces:
  - `DockerCliResult(exit_code: int, stdout: str, stderr: str)`
  - `DockerCli` Protocol: `async capture(argv: Sequence[str]) -> DockerCliResult` and `async combined(argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome`
  - `SubprocessDockerCli` — the real implementation
  - `DockerUnavailable(RuntimeError)`
  - `ContainerRegistry(image, workspace_mount, cli)` with `async acquire(workspace: Path) -> str` and `async aclose() -> None`
  - `DockerSandbox` — implements `Sandbox`
  - `DockerSandboxFactory(image: str, workspace_mount: str, cli: DockerCli | None = None)` — implements `SandboxFactory` via `__call__(workspace) -> Sandbox`, plus `async aclose() -> None`

Design notes to honour:
- **`docker exec` argv:** `["docker", "exec", "-w", workspace_mount, *env_flags, container_id, "sh", "-c", command]` where `env_flags` is `["-e", f"{k}={v}", ...]`. stderr is merged into stdout on the **host side** (the `docker exec` client's `stderr=STDOUT`), exactly as the local runner does — do **not** wrap the user's command in `2>&1`, which would change its shell semantics.
- **`docker run` argv:** `["docker", "run", "-d", "--name", container_name, "-v", f"{host_workspace}:{workspace_mount}", "-w", workspace_mount, image, "sleep", "infinity"]`.
- **Container name:** `f"milky-frog-{hashlib.sha256(str(workspace).encode()).hexdigest()[:12]}"` — deterministic per workspace, no reliance on path characters being name-safe.
- **`build_env()` does not forward host `HOME`/`PATH`/`SHELL`/`TERM`/`LANG`/`LC_ALL`/`TMPDIR`** — they name host filesystem locations meaningless inside a differently-shaped image. It returns `{"CI": "true", "GIT_TERMINAL_PROMPT": "0"}` plus each name in `config.env_allowlist_extra` present in the host `os.environ` (opt-in tokens/build vars: the *value* travels, not a host path).
- **Timeout** is enforced on the host-side `docker exec` process. The in-container process may survive until the container is torn down; that is a documented MVP limitation, not a bug to fix here.

- [ ] **Step 1: Write the failing tests**

Create `tests/adapters/__init__.py` (empty file).

Create `tests/adapters/test_docker_sandbox.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandTimeout,
    SandboxViolation,
)
from milky_frog.project import ProjectConfig
from stubs import StubDockerCli


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

    await sandbox.run_command(
        "ls", timeout_seconds=5, presentation=CommandPresentation.TERMINAL
    )

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
```

Add to `tests/stubs.py`:

```python
@dataclass(frozen=True, slots=True)
class CombinedCall:
    """One recorded ``DockerCli.combined`` invocation."""

    argv: list[str]
    timeout_seconds: float


class StubDockerCli:
    """DockerCli double: records argv, returns canned results. No daemon needed."""

    def __init__(
        self,
        *,
        container_id: str = "container-1",
        outcome: CommandOutcome | None = None,
    ) -> None:
        self._container_id = container_id
        self._outcome = outcome if outcome is not None else CommandResult(0, "ok\n")
        self.captured: list[list[str]] = []
        self.combined_calls: list[CombinedCall] = []

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        self.captured.append(list(argv))
        stdout = f"{self._container_id}\n" if argv[:2] == ["docker", "run"] else ""
        return DockerCliResult(exit_code=0, stdout=stdout, stderr="")

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        self.combined_calls.append(CombinedCall(list(argv), timeout_seconds))
        return self._outcome
```

with these additions to `tests/stubs.py` imports:
```python
from collections.abc import Sequence
from dataclasses import dataclass

from milky_frog.adapters.docker.cli import DockerCliResult
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/adapters/test_docker_sandbox.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'milky_frog.adapters.docker'`

- [ ] **Step 3: Implement the Docker CLI seam**

Create `src/milky_frog/adapters/docker/cli.py`:

```python
"""The one place Milky Frog shells out to the ``docker`` binary.

Isolating it behind a Protocol keeps ``DockerSandbox`` unit-testable without a
running daemon: the tests inject a stub that records argv.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from milky_frog.adapters.process import make_command_result
from milky_frog.core.sandbox import CommandOutcome, CommandStartError, CommandTimeout

DOCKER_BINARY = "docker"


class DockerUnavailable(RuntimeError):
    """Raised when the ``docker`` CLI is missing or the daemon is unreachable."""


@dataclass(frozen=True, slots=True)
class DockerCliResult:
    """A finished ``docker`` invocation with stdout and stderr kept apart."""

    exit_code: int
    stdout: str
    stderr: str


class DockerCli(Protocol):
    """Seam over the ``docker`` binary."""

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        """Run a lifecycle command (``run``/``rm``/``version``), no timeout."""
        ...

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        """Run a command with stderr merged into stdout, under a timeout."""
        ...


class SubprocessDockerCli:
    """Default ``DockerCli``: ``asyncio.create_subprocess_exec`` on the host."""

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise DockerUnavailable(f"cannot run {DOCKER_BINARY}: {error}") from error

        stdout, stderr = await process.communicate()
        return DockerCliResult(
            exit_code=process.returncode if process.returncode is not None else 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as error:
            return CommandStartError(str(error))

        communicate = asyncio.create_task(process.communicate())
        try:
            stdout, _ = await asyncio.wait_for(
                asyncio.shield(communicate), timeout=timeout_seconds
            )
        except TimeoutError:
            # Kills the host-side `docker exec` client. The in-container
            # process may survive until the container itself is removed.
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await communicate
            return CommandTimeout(timeout_seconds)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise

        return make_command_result(
            process.returncode if process.returncode is not None else 0,
            stdout if stdout is not None else b"",
        )


async def docker_is_available(cli: DockerCli | None = None) -> bool:
    """Whether ``docker version`` succeeds. Used by ``doctor`` and integration tests."""
    runner = cli if cli is not None else SubprocessDockerCli()
    try:
        result = await runner.capture([DOCKER_BINARY, "version", "--format", "{{.Server.Version}}"])
    except DockerUnavailable:
        return False
    return result.exit_code == 0
```

- [ ] **Step 4: Implement the sandbox and factory**

Create `src/milky_frog/adapters/docker/sandbox.py`:

```python
"""Container Sandbox: bind-mount the Workspace, run commands via ``docker exec``.

Path resolution and the sensitive-path deny policy are **not** reimplemented —
they are delegated to a composed ``LocalSandbox``. Because the Workspace is
bind-mounted at ``workspace_mount``, host-side file I/O (what ``read_file`` /
``write_file`` / ``grep`` already do) and container-side commands observe the
same bytes. Only command execution differs, which is exactly the seam
``Sandbox.run_command`` covers.

This is a policy boundary with real process isolation for commands, not a
security boundary against a fully-untrusted model: the bind mount means a
process in the container can still reach every file in the Workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from milky_frog.adapters.docker.cli import (
    DOCKER_BINARY,
    DockerCli,
    DockerUnavailable,
    SubprocessDockerCli,
)
from milky_frog.adapters.local import LocalSandbox
from milky_frog.adapters.process import with_presentation_env
from milky_frog.core.sandbox import CommandOutcome, CommandPresentation, Sandbox
from milky_frog.project import ProjectConfig

_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
}


def _container_name(workspace: Path) -> str:
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"milky-frog-{digest}"


class ContainerRegistry:
    """Owns the container lifecycle for one image, keyed by Workspace.

    A container is created lazily on first use and reused for every subsequent
    command — ``docker exec`` costs tens of milliseconds where a fresh
    ``docker run`` costs hundreds. ``aclose()`` removes everything it created.
    """

    def __init__(self, *, image: str, workspace_mount: str, cli: DockerCli) -> None:
        self._image = image
        self._workspace_mount = workspace_mount
        self._cli = cli
        self._containers: dict[Path, str] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, workspace: Path) -> str:
        async with self._lock:
            existing = self._containers.get(workspace)
            if existing is not None:
                return existing
            container_id = await self._start(workspace)
            self._containers[workspace] = container_id
            return container_id

    async def _start(self, workspace: Path) -> str:
        result = await self._cli.capture(
            [
                DOCKER_BINARY,
                "run",
                "-d",
                "--name",
                _container_name(workspace),
                "-v",
                f"{workspace}:{self._workspace_mount}",
                "-w",
                self._workspace_mount,
                self._image,
                "sleep",
                "infinity",
            ]
        )
        if result.exit_code != 0:
            raise DockerUnavailable(
                f"failed to start container from image {self._image!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise DockerUnavailable("docker run returned no container id")
        return container_id

    async def aclose(self) -> None:
        async with self._lock:
            containers = list(self._containers.values())
            self._containers.clear()
        for container_id in containers:
            await self._cli.capture([DOCKER_BINARY, "rm", "-f", container_id])


class DockerSandbox:
    """Sandbox adapter that executes commands inside a container."""

    def __init__(
        self,
        workspace: Path,
        config: ProjectConfig | None = None,
        *,
        workspace_mount: str,
        containers: ContainerRegistry,
        cli: DockerCli,
    ) -> None:
        self._local = LocalSandbox(workspace, config)
        self._workspace_mount = workspace_mount
        self._containers = containers
        self._cli = cli
        self.workspace = self._local.workspace
        self.config = self._local.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        """Delegate to the composed LocalSandbox: same deny policy, host path.

        The Workspace is bind-mounted, so the host path a Tool reads and the
        container path a command sees refer to the same file.
        """
        return self._local.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        """Container env: non-interactive defaults plus opt-in host values.

        Host ``HOME`` / ``PATH`` / ``SHELL`` are deliberately *not* forwarded —
        they name host filesystem locations that mean nothing inside the image.
        ``env_allowlist_extra`` names (build vars, tokens) do travel, because
        their *value* is what matters, not a path.
        """
        env = dict(_NONINTERACTIVE_ENV)
        for name in self.config.env_allowlist_extra:
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        return env

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        container_id = await self._containers.acquire(self.workspace)
        env = self.build_env()
        if presentation is CommandPresentation.TERMINAL:
            env = with_presentation_env(env)

        env_flags: list[str] = []
        for name, value in env.items():
            env_flags.extend(("-e", f"{name}={value}"))

        argv = [
            DOCKER_BINARY,
            "exec",
            "-w",
            self._workspace_mount,
            *env_flags,
            container_id,
            "sh",
            "-c",
            command,
        ]
        return await self._cli.combined(argv, timeout_seconds=timeout_seconds)


class DockerSandboxFactory:
    """``SandboxFactory`` producing ``DockerSandbox`` over a shared container registry.

    One factory per session. ``aclose()`` (wired into ``ShutdownManager``)
    removes every container it started.
    """

    def __init__(
        self,
        *,
        image: str,
        workspace_mount: str,
        cli: DockerCli | None = None,
        config: ProjectConfig | None = None,
    ) -> None:
        self._cli = cli if cli is not None else SubprocessDockerCli()
        self._workspace_mount = workspace_mount
        self._config = config
        self._containers = ContainerRegistry(
            image=image, workspace_mount=workspace_mount, cli=self._cli
        )

    def __call__(self, workspace: Path) -> Sandbox:
        return DockerSandbox(
            workspace,
            self._config,
            workspace_mount=self._workspace_mount,
            containers=self._containers,
            cli=self._cli,
        )

    async def aclose(self) -> None:
        await self._containers.aclose()
```

Create `src/milky_frog/adapters/docker/__init__.py`:

```python
from milky_frog.adapters.docker.cli import (
    DockerCli,
    DockerCliResult,
    DockerUnavailable,
    SubprocessDockerCli,
    docker_is_available,
)
from milky_frog.adapters.docker.sandbox import (
    ContainerRegistry,
    DockerSandbox,
    DockerSandboxFactory,
)

__all__ = [
    "ContainerRegistry",
    "DockerCli",
    "DockerCliResult",
    "DockerSandbox",
    "DockerSandboxFactory",
    "DockerUnavailable",
    "SubprocessDockerCli",
    "docker_is_available",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/adapters/test_docker_sandbox.py -v --no-cov`
Expected: PASS (all 11 tests)

- [ ] **Step 6: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass. If coverage drops below 80 because `SubprocessDockerCli` is unexercised, that is expected until Task 8 — check the delta is confined to `adapters/docker/cli.py`.

- [ ] **Step 7: Commit**

```bash
git add src/milky_frog/adapters/docker/ tests/adapters/ tests/stubs.py
git commit -m "feat: add DockerSandbox adapter"
```

---

## Task 6: Wire the factory into session assembly and shutdown

**Files:**
- Modify: `src/milky_frog/core/runtime/assemble.py`
- Modify: `src/milky_frog/app/session.py`
- Modify: `src/milky_frog/core/shutdown.py`
- Test: `tests/test_agent_session.py`, `tests/test_shutdown.py`

**Interfaces:**
- Consumes: `SandboxConfig` / `ProjectConfig.sandbox` (Task 4), `DockerSandboxFactory` (Task 5).
- Produces: `make_sandbox_factory(config: ProjectConfig) -> SandboxFactory` in `core/runtime/assemble.py`.

Design note: `AgentSessionConfig.sandbox_factory` keeps its default of `LocalSandbox`. Its type widens to `SandboxFactory | None`; `None` means "derive from project config", which is what `AgentSession.__aenter__` now passes when the caller didn't override it. Tests that inject a stub factory keep working untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_session.py`:

```python
def test_make_sandbox_factory_returns_local_by_default() -> None:
    factory = make_sandbox_factory(ProjectConfig())

    assert factory is LocalSandbox


def test_make_sandbox_factory_returns_docker_when_configured() -> None:
    config = ProjectConfig(
        sandbox=SandboxConfig(kind="docker", image="python:3.12", workspace_mount="/mnt/ws")
    )

    factory = make_sandbox_factory(config)

    assert isinstance(factory, DockerSandboxFactory)
```

with imports:
```python
from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.runtime.assemble import make_sandbox_factory
from milky_frog.project import ProjectConfig, SandboxConfig
```

Append to `tests/test_shutdown.py`, reusing the `_FakeForeground` / `_FakeModel`
doubles already defined at the top of that file (and matching its existing
`# type: ignore[arg-type]` on `wire`):

```python
async def test_cleanup_closes_sandbox_factory() -> None:
    mgr = ShutdownManager()
    factory = ClosingSandboxFactory()
    mgr.wire(_FakeForeground(), [], _FakeModel(), sandbox_factory=factory)  # type: ignore[arg-type]

    await mgr.cleanup(None, None, None)

    assert factory.closed is True


async def test_cleanup_tolerates_factory_without_aclose() -> None:
    mgr = ShutdownManager()
    mgr.wire(_FakeForeground(), [], _FakeModel(), sandbox_factory=LocalSandbox)  # type: ignore[arg-type]

    await mgr.cleanup(None, None, None)  # LocalSandbox has no aclose(); must not raise


async def test_cleanup_without_sandbox_factory_is_a_noop() -> None:
    mgr = ShutdownManager()
    mgr.wire(_FakeForeground(), [], _FakeModel())  # type: ignore[arg-type]

    await mgr.cleanup(None, None, None)  # must not raise
```

with imports `from milky_frog.adapters.local import LocalSandbox` and
`from stubs import ClosingSandboxFactory`.

Add to `tests/stubs.py`:

```python
class ClosingSandboxFactory:
    """SandboxFactory that records whether aclose() was awaited."""

    def __init__(self) -> None:
        self.closed = False

    def __call__(self, workspace: Path) -> Sandbox:
        return LocalSandbox(workspace)

    async def aclose(self) -> None:
        self.closed = True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_session.py tests/test_shutdown.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'make_sandbox_factory'`

- [ ] **Step 3: Add `make_sandbox_factory`**

In `src/milky_frog/core/runtime/assemble.py`, add the import and the function above `make_session_handlers`:

```python
from milky_frog.project import ProjectConfig


def make_sandbox_factory(config: ProjectConfig) -> SandboxFactory:
    """Pick the Sandbox adapter named by ``[sandbox].kind`` in the project config.

    ``local`` (default) returns the ``LocalSandbox`` class itself — it already
    satisfies ``SandboxFactory`` via its ``(workspace)`` constructor.
    """
    if config.sandbox.kind == "docker":
        from milky_frog.adapters.docker import DockerSandboxFactory

        image = config.sandbox.image
        if image is None:  # pragma: no cover - SandboxConfig validation guarantees this
            raise ValueError("sandbox.image is required when sandbox.kind = 'docker'")
        return DockerSandboxFactory(
            image=image,
            workspace_mount=config.sandbox.workspace_mount,
            config=config,
        )
    return LocalSandbox
```

The `DockerSandboxFactory` import is function-local so importing `assemble` never pulls in the docker adapter for the common local path.

- [ ] **Step 4: Widen `ShutdownManager.wire()`**

In `src/milky_frog/core/shutdown.py`:

Add to the `TYPE_CHECKING` block:
```python
    from milky_frog.core.sandbox import SandboxFactory
```

Add to `__init__`:
```python
        self._sandbox_factory: SandboxFactory | None = None
```

Change the `wire` signature and body:
```python
    def wire(
        self,
        foreground: ForegroundRun,
        handlers: list[Handler],
        model: OpenAIModel,
        *,
        sandbox_factory: SandboxFactory | None = None,
    ) -> None:
        """Bind the runtime resources this manager controls.

        Called once from ``AgentSession.__aenter__`` after ``ForegroundRun``,
        handler list, and model client have been created. ``sandbox_factory``
        is released in ``cleanup()`` when it exposes ``aclose()`` (the container
        Sandbox does; ``LocalSandbox`` does not).
        """
        self._foreground = foreground
        self._handlers = handlers
        self._model = model
        self._sandbox_factory = sandbox_factory
        if self._shutdown_requested:
            self.shutdown_run()
```

Append to `cleanup()`, after the model teardown:
```python
        factory = self._sandbox_factory
        aclose = getattr(factory, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                logger.exception("Sandbox factory cleanup failed")
```

- [ ] **Step 5: Build the factory in `AgentSession`**

In `src/milky_frog/app/session.py`:

Change the dataclass field:
```python
@dataclass(frozen=True, slots=True)
class AgentSessionConfig:
    """Session-level policy passed to ``AgentSession`` at construction time."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    sandbox_factory: SandboxFactory | None = None
```

Add the import:
```python
from milky_frog.core.runtime.assemble import make_sandbox_factory
```
(if `assemble` is already imported for `make_agent_harness` / `make_session_handlers`, extend that import instead)

In `__aenter__`, immediately after `project_cfg = load_project_config(workspace)`:
```python
        sandbox_factory = self._config.sandbox_factory or make_sandbox_factory(project_cfg)
```

Replace both `sandbox_factory=self._config.sandbox_factory` occurrences (the `make_session_handlers` and `make_agent_harness` calls) with `sandbox_factory=sandbox_factory`.

Change the wire call:
```python
            self._shutdown.wire(
                self._foreground,
                self._handlers,
                self._model,
                sandbox_factory=sandbox_factory,
            )
```

Note: `AgentSessionConfig.sandbox_factory` now defaults to `None`, so drop the now-unused `LocalSandbox` import from `session.py` if nothing else in the file uses it (`grep -n LocalSandbox src/milky_frog/app/session.py`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_session.py tests/test_shutdown.py -v --no-cov`
Expected: PASS

- [ ] **Step 7: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass. Any test that constructed `AgentSessionConfig(sandbox_factory=SomeStub())` still works — only the default changed.

- [ ] **Step 8: Commit**

```bash
git add src/milky_frog/core/runtime/assemble.py src/milky_frog/app/session.py \
        src/milky_frog/core/shutdown.py tests/test_agent_session.py tests/test_shutdown.py tests/stubs.py
git commit -m "feat: select Sandbox adapter from project config and close it on shutdown"
```

---

## Task 7: Surface config and Docker problems at startup

`doctor` gains a Docker check; the interactive entry point refuses to start on a broken `[sandbox]` table rather than silently running unsandboxed.

**Files:**
- Modify: `src/milky_frog/cli/actions.py`
- Modify: `src/milky_frog/cli/commands.py`
- Modify: `src/milky_frog/cli/launch.py`
- Test: `tests/cli/test_cli.py`

**Interfaces:**
- Consumes: `validate_sandbox_config`, `SandboxConfigError` (Task 4); `docker_is_available` (Task 5); `Diagnostic`, `CheckStatus` (`milky_frog/diagnostics.py`).
- Produces: `build_doctor_diagnostics(settings, workspace: Path | None = None) -> tuple[Diagnostic, ...]` — new optional second parameter, defaulting to `Path.cwd()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_cli.py`. There are no doctor tests today, so these
helpers are new — add them near the top of the file:

```python
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
```

with imports:
```python
from milky_frog.cli.actions import build_doctor_diagnostics
from milky_frog.diagnostics import CheckStatus
from milky_frog.project import CONFIG_FILENAME, PROJECT_DIRNAME
from milky_frog.settings import Settings
```
(`Settings` is likely already imported — check before adding.)

Then the tests. Note `_settings()` takes `tmp_path` so `home` never touches the
real state dir:

```python
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
```

`build_doctor_diagnostics` becomes `async` and takes an injected
`docker_available: bool | None = None` so the test never touches a daemon (when
`None`, it calls `docker_is_available()`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/ -v --no-cov -k sandbox`
Expected: FAIL — `TypeError: build_doctor_diagnostics() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Add the diagnostic**

In `src/milky_frog/cli/actions.py`, add imports:
```python
from milky_frog.adapters.docker import docker_is_available
from milky_frog.project import SandboxConfigError, load_project_config, validate_sandbox_config
```
(extend the existing `milky_frog.project` import rather than adding a second one)

Replace `build_doctor_diagnostics` with:

```python
async def _sandbox_diagnostic(workspace: Path, docker_available: bool | None) -> Diagnostic:
    try:
        validate_sandbox_config(workspace)
    except SandboxConfigError as error:
        return Diagnostic("Sandbox", CheckStatus.FAIL, str(error))

    config = load_project_config(workspace)
    if config.sandbox.kind == "local":
        return Diagnostic("Sandbox", CheckStatus.PASS, "local")

    available = docker_available if docker_available is not None else await docker_is_available()
    if not available:
        return Diagnostic(
            "Sandbox",
            CheckStatus.FAIL,
            "docker configured but the docker daemon is unreachable",
        )
    return Diagnostic("Sandbox", CheckStatus.PASS, f"docker ({config.sandbox.image})")


async def build_doctor_diagnostics(
    settings: Settings,
    workspace: Path | None = None,
    *,
    docker_available: bool | None = None,
) -> tuple[Diagnostic, ...]:
    sandbox = await _sandbox_diagnostic(workspace or Path.cwd(), docker_available)
    return (
        Diagnostic("State directory", CheckStatus.PASS, str(settings.home)),
        Diagnostic(
            "API key",
            CheckStatus.PASS if settings.api_key else CheckStatus.FAIL,
            "configured" if settings.api_key else "missing (MILKY_FROG_API_KEY)",
        ),
        Diagnostic(
            "Base URL",
            CheckStatus.PASS if settings.base_url else CheckStatus.WARN,
            settings.base_url or "provider default",
        ),
        Diagnostic(
            "Model",
            CheckStatus.PASS if settings.model else CheckStatus.FAIL,
            settings.model or "missing (MILKY_FROG_MODEL)",
        ),
        sandbox,
    )
```

- [ ] **Step 4: Update the `doctor` command**

In `src/milky_frog/cli/commands.py`, `doctor()` must now drive the coroutine and fail on a FAIL sandbox check:

```python
def doctor() -> None:
    """Check local configuration without making a model request."""
    settings = Settings.from_environment()
    diagnostics = asyncio.run(build_doctor_diagnostics(settings))
    render_diagnostics(diagnostics)
    if not settings.api_key or not settings.model:
        render_configuration_error(run_doctor_again=True)
        raise typer.Exit(code=2)
    if any(d.status is CheckStatus.FAIL for d in diagnostics):
        raise typer.Exit(code=2)
```
Add `import asyncio` and `from milky_frog.diagnostics import CheckStatus` to that module.

- [ ] **Step 5: Refuse to launch on a broken `[sandbox]` table**

In `src/milky_frog/cli/launch.py`:

```python
from pathlib import Path

from milky_frog.project import SandboxConfigError, validate_sandbox_config


def interactive(*, launch: TuiLaunch | None = None) -> None:
    """Run the foreground interactive loop in full-screen TUI mode."""
    settings = Settings.from_environment()
    require_model_configuration_or_exit(settings)
    require_valid_sandbox_config_or_exit(Path.cwd())
    run_tui(settings, launch=launch)


def require_valid_sandbox_config_or_exit(workspace: Path) -> None:
    """Stop before a Run starts if [sandbox] is broken.

    Falling back to LocalSandbox here would silently run unsandboxed for a
    user who asked for container isolation — so this exits instead.
    """
    try:
        validate_sandbox_config(workspace)
    except SandboxConfigError as error:
        render_error(str(error), hint="Fix [sandbox] in .milky-frog/config.toml, then run doctor.")
        raise typer.Exit(code=2) from None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/cli/ -v --no-cov`
Expected: PASS. There are no pre-existing `build_doctor_diagnostics` tests, but grep for other callers before assuming: `grep -rn "build_doctor_diagnostics" src/ tests/` — every caller must now `await` it. The suite runs `asyncio_mode=auto`, so an `async def test_` needs no marker.

- [ ] **Step 7: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/milky_frog/cli/ tests/cli/
git commit -m "feat: report Sandbox configuration in doctor and gate startup"
```

---

## Task 8: Live Docker integration test

Proves the adapter works against a real daemon and pins the acceptance criteria. Skipped automatically when Docker is absent, so it never breaks local runs or CI without Docker.

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_docker_sandbox_live.py`

**Interfaces:**
- Consumes: `DockerSandboxFactory` (Task 5, real `SubprocessDockerCli`), `BashTool` (Task 2), `ReadTool`/`GrepTool` from `harness/tools/builtins`, `ToolContext`.

Notes:
- Uses image `alpine:3.20` — small, quick to pull, has `sh`, `cat`, `grep`.
- Guarded by an async check, not just `shutil.which("docker")`: the binary can exist with a dead daemon.
- Always `await factory.aclose()` in a `finally` so a failing assertion never leaks a container.
- Confirm the exact class names of the read/grep tools first: `grep -n "^class" src/milky_frog/harness/tools/builtins/read.py src/milky_frog/harness/tools/builtins/grep.py`.

- [ ] **Step 1: Write the test**

Create `tests/integration/__init__.py` (empty).

Create `tests/integration/test_docker_sandbox_live.py`:

```python
"""Live Docker tests. Skipped unless a real daemon answers `docker version`.

These are the only tests that touch a container; everything else stubs the
`DockerCli` seam. Keep them few and fast.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.adapters.docker import DockerSandboxFactory, docker_is_available
from milky_frog.core.sandbox import CommandResult, CommandTimeout, SandboxViolation
from milky_frog.harness.tools.base import ToolContext

IMAGE = "alpine:3.20"


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    return asyncio.run(docker_is_available())


pytestmark = pytest.mark.skipif(
    not _docker_reachable(), reason="docker daemon not reachable"
)


@pytest.fixture
async def factory() -> AsyncIterator[DockerSandboxFactory]:
    made = DockerSandboxFactory(image=IMAGE, workspace_mount="/mnt/workspace")
    try:
        yield made
    finally:
        await made.aclose()


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


async def test_bash_tool_end_to_end_in_container(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    from milky_frog.harness.tools.builtins.bash import BashTool

    context = ToolContext("run-1", tmp_path, sandbox=factory(tmp_path))

    result = await BashTool().execute(context, BashTool.input_model(command="echo hi"))

    assert not result.is_error
    assert "hi" in result.content


async def test_bash_nonzero_exit_is_error(tmp_path: Path, factory: DockerSandboxFactory) -> None:
    from milky_frog.harness.tools.builtins.bash import BashTool

    context = ToolContext("run-1", tmp_path, sandbox=factory(tmp_path))

    result = await BashTool().execute(context, BashTool.input_model(command="exit 3"))

    assert result.is_error
    assert "exit code 3" in result.content


async def test_read_and_grep_work_over_the_bind_mount(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    """File Tools stay host-side; the mount keeps them consistent with bash."""
    (tmp_path / "app.py").write_text("def handler():\n    return 42\n", encoding="utf-8")
    sandbox = factory(tmp_path)

    assert sandbox.resolve("app.py") == tmp_path / "app.py"

    outcome = await sandbox.run_command("grep -n handler app.py", timeout_seconds=30)
    assert isinstance(outcome, CommandResult)
    assert "def handler" in outcome.output


async def test_sensitive_paths_are_denied_like_local(
    tmp_path: Path, factory: DockerSandboxFactory
) -> None:
    sandbox = factory(tmp_path)

    with pytest.raises(SandboxViolation):
        sandbox.resolve(".env")
    with pytest.raises(SandboxViolation):
        sandbox.resolve("../secret")


async def test_command_timeout_in_container(tmp_path: Path, factory: DockerSandboxFactory) -> None:
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("sleep 30", timeout_seconds=2)

    assert isinstance(outcome, CommandTimeout)
    assert outcome.seconds == 2


async def test_host_env_does_not_leak_into_container(
    tmp_path: Path, factory: DockerSandboxFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MILKY_FROG_API_KEY", "super-secret")
    sandbox = factory(tmp_path)

    outcome = await sandbox.run_command("env", timeout_seconds=30)

    assert isinstance(outcome, CommandResult)
    assert "super-secret" not in outcome.output
```

If `ToolContext("run-1", tmp_path, sandbox=...)` doesn't match the real signature, copy the exact construction from `_context()` in `tests/harness/test_builtin_tools.py:19`.

- [ ] **Step 2: Run the test with Docker present**

Run: `uv run pytest tests/integration/ -v --no-cov`
Expected (Docker running): PASS, 8 tests. First run pulls `alpine:3.20`, so allow ~30s.
Expected (Docker absent): all SKIPPED with "docker daemon not reachable".

If Docker isn't available in your environment, run `docker version` to confirm, then verify the skip path is what triggers — do not mark the task done on a skip alone if Docker *is* available.

- [ ] **Step 3: Confirm the suite still passes without Docker**

Run: `env PATH=/usr/bin:/bin uv run pytest tests/integration/ -v --no-cov`
Expected: SKIPPED (proves the guard works when `docker` is off `PATH`).

- [ ] **Step 4: Run the full check suite**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "test: add live Docker sandbox integration tests"
```

---

## Task 9: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md` (§6 seam table row; §7)
- Modify: `CONTEXT.md` (glossary, both the English and 中文 sections)
- Modify: `README.md`
- Modify: `CLAUDE.md` (the `harness/sandbox/` seam bullet)

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

In the §6 seam table, replace the Sandbox row:
```markdown
| **Sandbox** | `Sandbox` (`core/sandbox.py`) | `LocalSandbox`, injected via `sandbox_factory` | `DockerSandbox` (`adapters/docker/`) swaps this one seam for container execution; selected by `[sandbox].kind` in `.milky-frog/config.toml`. |
```

In §7, retitle the section `## 7. The Sandbox policy` and replace its body's last paragraph, then append:

```markdown
It does **not** contain untrusted code. Under `LocalSandbox` a determined
process still runs on the host. The boundary exists to stop a cooperating model
from *accidentally* touching something sensitive, and to put a human in the loop
for shell.

Both adapters route **every** shell command through `Sandbox.run_command()` —
`bash` (`harness/tools/builtins/bash.py`) and the post-edit
`VerificationHandler`. Nothing else in the codebase spawns a command. (MCP
servers are the one live exception: `McpClientManager` spawns its own stdio
subprocesses, because a long-lived piped process needs a `spawn()`-shaped seam
this protocol does not yet have. Tracked separately.)

### The Container Sandbox

`DockerSandbox` (`adapters/docker/`) is the opt-in alternative, enabled by:

```toml
[sandbox]
kind = "docker"
image = "python:3.12-bookworm"
workspace_mount = "/mnt/workspace"   # must live under /mnt
```

- The Workspace is **bind-mounted** at `workspace_mount`. `resolve()` therefore
  still returns a host path and delegates to a composed `LocalSandbox` — the
  deny-pattern policy is identical, and `read_file` / `write_file` / `edit_file`
  / `grep` / `list_dir` need no container awareness at all.
- `run_command()` is the only container-specific method: a container is created
  lazily per Workspace (`docker run -d … sleep infinity`) and reused for every
  subsequent `docker exec`. `DockerSandboxFactory.aclose()`, wired into
  `ShutdownManager`, removes them.
- `build_env()` does **not** forward host `HOME`/`PATH`/`SHELL` — those name host
  filesystem locations. `env_allowlist_extra` values do travel.
- Command execution is genuinely isolated; **file access is not**. A process in
  the container reaches the whole bind-mounted Workspace. This remains a policy
  boundary, not a defence against a hostile model.
- A timeout kills the host-side `docker exec` client. The in-container process
  may linger until the container is removed at session end.
```

- [ ] **Step 2: Update `CONTEXT.md`**

After the **Local Sandbox** entry, add:

```markdown
**Container Sandbox**:
A Sandbox adapter that executes commands inside a container against a bind-mounted Workspace. Isolates command execution; does not isolate file access. Implemented by `DockerSandbox` (`adapters/docker/`), opt-in via `[sandbox].kind = "docker"`.
_Avoid_: DockerExecutionBackend, execution backend, secure sandbox
```

Fix the stale path in the existing **Local Sandbox** entry: `harness/sandbox/` → `adapters/local/`.

Add the matching 中文 entry after **Local Sandbox（本地沙箱）**:

```markdown
**Container Sandbox（容器沙箱）**：
在容器内针对 bind-mount 的 Workspace 执行命令的 Sandbox 适配器。隔离命令执行，不隔离文件访问。由 `DockerSandbox`（`adapters/docker/`）实现，通过 `[sandbox].kind = "docker"` 选择启用。
_避免_：DockerExecutionBackend、execution backend、secure sandbox
```

- [ ] **Step 3: Update `README.md`**

Add a section after the configuration section (find it with `grep -n "^## " README.md`):

````markdown
## Containerized execution (opt-in)

By default Milky Frog runs Tools on the host under a path-deny policy. To run
`bash` and post-edit verification commands inside a container instead, add to
`.milky-frog/config.toml`:

```toml
[sandbox]
kind = "docker"
image = "python:3.12-bookworm"
workspace_mount = "/mnt/workspace"   # optional; must live under /mnt
```

Requires the `docker` CLI on `PATH` and a running daemon — `milky-frog doctor`
checks both.

How it works: your workspace is bind-mounted into a container that is created
on first use and reused for the rest of the session, then removed on exit.
File Tools (`read_file`, `write_file`, `edit_file`, `grep`, `list_dir`) keep
reading and writing on the host — the bind mount means both sides see the same
files.

Caveats:

- **File access is not isolated.** A process in the container can reach every
  file in the bind-mounted workspace. This isolates *command execution*, not
  the workspace.
- Host `HOME` / `PATH` / `SHELL` are not forwarded into the container. Names
  listed in `env_allowlist_extra` are.
- A `bash` timeout kills the host-side `docker exec`; the process inside the
  container may keep running until the container is removed at session exit.
- MCP servers still run on the host, not in the container.
````

- [ ] **Step 4: Update `CLAUDE.md`**

Replace the `harness/sandbox/` seam bullet with:

```markdown
- `adapters/local/`, `adapters/docker/` — `Sandbox` protocol (`core/sandbox.py`)
  + `LocalSandbox` (path deny patterns, subprocess env, `run_command`) and the
  opt-in `DockerSandbox` (bind-mount + `docker exec`), selected by
  `[sandbox].kind`. Implements the **Sandbox** policy; a policy boundary,
  **not** host isolation. Every shell command in the codebase goes through
  `Sandbox.run_command()`.
```

- [ ] **Step 5: Verify docs match reality**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
Expected: all pass.

Manually confirm each path named in the docs exists:
```bash
ls src/milky_frog/adapters/docker/ src/milky_frog/adapters/local/ src/milky_frog/core/sandbox.py
grep -n "sandbox" src/milky_frog/project.py | head
```

- [ ] **Step 6: Commit**

```bash
git add docs/ARCHITECTURE.md CONTEXT.md README.md CLAUDE.md
git commit -m "docs: document the Container Sandbox"
```

---

## Final verification

- [ ] **Full suite:** `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyrefly check`
- [ ] **Live Docker path:** `uv run pytest tests/integration/ -v --no-cov` (with the daemon running)
- [ ] **Real end-to-end:** in a scratch directory with `[sandbox] kind = "docker"`, run `uv run milky-frog doctor` — expect a `Sandbox … PASS docker (…)` line. Then start `uv run milky-frog` and ask it to run `cat /etc/os-release`; the output should name the container's distro, not the host's.
- [ ] **No stray containers:** `docker ps -a --filter name=milky-frog` is empty after the session exits.
- [ ] **Grep for escapes:** `grep -rn "create_subprocess" src/milky_frog/` returns only `adapters/local/command.py` and `adapters/docker/cli.py`.

## Acceptance criteria (issue #60)

- [ ] Containerized `Sandbox` implementation + factory injectable via `AgentSessionConfig.sandbox_factory` — Tasks 5, 6
- [ ] `bash` executes in the container; cwd is the mounted workspace; timeout/truncation match local — Tasks 2, 5, 8
- [ ] `resolve()` rejects `.env` / `.git` etc., same as `LocalSandbox` — Tasks 5, 8
- [ ] `read_file` / `grep` behave predictably; mount assumptions documented — Tasks 8, 9
- [ ] Docs explain how to enable the container backend — Task 9
- [ ] Tests + `ruff` + `pyrefly` pass — every task
