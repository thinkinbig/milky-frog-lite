from pathlib import Path

import pytest

from milky_frog.harness.sandbox import LocalSandbox, SandboxViolation


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
