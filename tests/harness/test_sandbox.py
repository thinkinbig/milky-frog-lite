from pathlib import Path

import pytest

from milky_frog.harness.sandbox import LocalSandbox, SandboxViolation
from milky_frog.project import ProjectConfig


def test_sandbox_resolves_workspace_file(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)

    assert sandbox.resolve("src/app.py") == tmp_path / "src/app.py"


@pytest.mark.parametrize("path", ["../secret", ".env", ".git/config", "private.key"])
def test_sandbox_rejects_escape_and_sensitive_paths(tmp_path: Path, path: str) -> None:
    sandbox = LocalSandbox(tmp_path)

    with pytest.raises(SandboxViolation):
        sandbox.resolve(path)


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


def test_sandbox_build_env_disables_pagers(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    env = sandbox.build_env()

    assert env["PAGER"] == "cat"
    assert env["GIT_PAGER"] == "cat"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_VALUE_0"] == "cat"


def test_noninteractive_defaults_win_over_allowlisted_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A forwarded GIT_PAGER must not re-enable a pager: the non-interactive
    # defaults are applied last and override host/allowlist values.
    monkeypatch.setenv("GIT_PAGER", "less")
    sandbox = LocalSandbox(tmp_path, ProjectConfig(env_allowlist_extra=("GIT_PAGER",)))

    assert sandbox.build_env()["GIT_PAGER"] == "cat"


def test_sandbox_loads_config_from_workspace_when_omitted(tmp_path: Path) -> None:
    # Without an explicit config the sandbox reads .milky-frog/config.toml, so
    # the default (workspace) -> sandbox factory still honours env_allowlist_extra.
    (tmp_path / ".milky-frog").mkdir()
    (tmp_path / ".milky-frog" / "config.toml").write_text(
        'env_allowlist_extra = ["MY_BUILD_VAR"]\n', encoding="utf-8"
    )
    sandbox = LocalSandbox(tmp_path)

    assert sandbox.config.env_allowlist_extra == ("MY_BUILD_VAR",)
