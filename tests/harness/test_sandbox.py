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


def test_sandbox_resolves_workspace_file(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)

    assert sandbox.resolve("src/app.py") == tmp_path / "src/app.py"


@pytest.mark.parametrize(
    "path",
    ["../secret", ".env", ".git/config", "private.key", ".milky-frog/mcp.json"],
)
def test_sandbox_rejects_escape_and_sensitive_paths(tmp_path: Path, path: str) -> None:
    sandbox = LocalSandbox(tmp_path)

    with pytest.raises(SandboxViolation):
        sandbox.resolve(path)


def test_sandbox_allows_committable_milky_frog_paths(tmp_path: Path) -> None:
    milky_frog = tmp_path / ".milky-frog"
    milky_frog.mkdir()
    (milky_frog / "config.toml").write_text("max_model_calls = 10\n", encoding="utf-8")
    skills = milky_frog / "skills" / "demo"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: demo\ndescription: x\n---\n", encoding="utf-8")
    tool_output = milky_frog / "tool-output"
    tool_output.mkdir()
    (tool_output / "spill.txt").write_text("spilled", encoding="utf-8")

    sandbox = LocalSandbox(tmp_path)

    assert sandbox.resolve(".milky-frog/config.toml") == milky_frog / "config.toml"
    assert sandbox.resolve(".milky-frog/skills/demo/SKILL.md") == skills / "SKILL.md"
    assert sandbox.resolve(".milky-frog/tool-output/spill.txt") == tool_output / "spill.txt"


def test_sandbox_applies_project_ignore_file(tmp_path: Path) -> None:
    (tmp_path / ".milkyfrogignore").write_text("secrets/**\n", encoding="utf-8")
    sandbox = LocalSandbox(tmp_path)

    with pytest.raises(SandboxViolation):
        sandbox.resolve("secrets/token.txt")


def test_sandbox_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    sandbox = LocalSandbox(tmp_path)

    with pytest.raises(SandboxViolation):
        sandbox.resolve("link/secret.txt")


def test_sandbox_build_env_disables_git_prompts_without_cat_fallback(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    env = sandbox.build_env()

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "PAGER" not in env
    assert "GIT_PAGER" not in env
    assert "MANPAGER" not in env
    assert "BROWSER" not in env
    assert "GIT_CONFIG_KEY_0" not in env


def test_noninteractive_defaults_win_over_allowlisted_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    sandbox = LocalSandbox(tmp_path, ProjectConfig(env_allowlist_extra=("GIT_TERMINAL_PROMPT",)))

    assert sandbox.build_env()["GIT_TERMINAL_PROMPT"] == "0"


def test_sandbox_loads_config_from_workspace_when_omitted(tmp_path: Path) -> None:
    # Without an explicit config the sandbox reads .milky-frog/config.toml, so
    # the default (workspace) -> sandbox factory still honours env_allowlist_extra.
    (tmp_path / ".milky-frog").mkdir()
    (tmp_path / ".milky-frog" / "config.toml").write_text(
        'env_allowlist_extra = ["MY_BUILD_VAR"]\n', encoding="utf-8"
    )
    sandbox = LocalSandbox(tmp_path)

    assert sandbox.config.env_allowlist_extra == ("MY_BUILD_VAR",)


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
