from __future__ import annotations

import json
from pathlib import Path

import pytest

from milky_frog.harness.mcp.config import (
    MCP_CONFIG_FILENAME,
    load_mcp_config,
    set_server_enabled,
)


def _write(path: Path, servers: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_load_mcp_config_merges_project_over_user(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(home / MCP_CONFIG_FILENAME, {"shared": {"command": "user-cmd", "enabled": False}})
    _write(
        workspace / ".milky-frog" / MCP_CONFIG_FILENAME,
        {"shared": {"command": "project-cmd", "enabled": True}},
    )

    cfg = load_mcp_config(home, workspace)

    assert cfg.mcpServers["shared"].command == "project-cmd"
    assert cfg.mcpServers["shared"].enabled is True


def test_set_server_enabled_edits_project_file_when_effective_entry_lives_there(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(home / MCP_CONFIG_FILENAME, {"shared": {"command": "user-cmd", "enabled": False}})
    _write(
        workspace / ".milky-frog" / MCP_CONFIG_FILENAME,
        {"shared": {"command": "project-cmd", "enabled": True}},
    )

    set_server_enabled(home, "shared", enabled=False, workspace=workspace)

    reloaded = load_mcp_config(home, workspace)
    assert reloaded.mcpServers["shared"].enabled is False
    # The user-level file must be untouched — the project entry was the
    # effective one, so editing the user-level copy would have been a no-op.
    user_data = json.loads((home / MCP_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert user_data["mcpServers"]["shared"]["enabled"] is False


def test_set_server_enabled_falls_back_to_user_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    _write(home / MCP_CONFIG_FILENAME, {"only-user": {"command": "cmd", "enabled": True}})

    set_server_enabled(home, "only-user", enabled=False, workspace=workspace)

    reloaded = load_mcp_config(home, workspace)
    assert reloaded.mcpServers["only-user"].enabled is False


def test_set_server_enabled_raises_key_error_when_missing_everywhere(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write(home / MCP_CONFIG_FILENAME, {"other": {"command": "cmd"}})

    with pytest.raises(KeyError):
        set_server_enabled(home, "missing", enabled=True, workspace=tmp_path / "project")


def test_set_server_enabled_raises_oserror_for_malformed_entry(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write(home / MCP_CONFIG_FILENAME, {"broken": "not-an-object"})

    with pytest.raises(OSError):
        set_server_enabled(home, "broken", enabled=True)
