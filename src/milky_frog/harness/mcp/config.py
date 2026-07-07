from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_serializer

logger = logging.getLogger(__name__)

MCP_CONFIG_FILENAME = "mcp.json"


class McpServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_serializer("args")
    def _serialize_args(self, v: tuple[str, ...]) -> list[str]:
        return list(v)


class McpConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)


def _user_config_path(home: Path) -> Path:
    return home / MCP_CONFIG_FILENAME


def _project_config_path(workspace: Path) -> Path:
    return workspace / ".milky-frog" / MCP_CONFIG_FILENAME


def _try_set_enabled(path: Path, name: str, *, enabled: bool) -> bool:
    """Toggle *name* in the mcp.json at *path* if it exists there.

    Returns ``True`` on success, ``False`` if *path* doesn't define *name*
    (so the caller can fall through to the next candidate file).
    """
    if not path.exists():
        return False
    try:
        data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OSError(f"cannot read {path}: {exc}") from exc

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False

    entry = servers[name]
    if not isinstance(entry, dict):
        raise OSError(f"malformed server entry {name!r} in {path}")

    entry["enabled"] = enabled
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def set_server_enabled(
    home: Path, name: str, *, enabled: bool, workspace: Path | None = None
) -> None:
    """Toggle a server's ``enabled`` flag in whichever file defines it.

    ``load_mcp_config`` lets a project-level entry override a same-named
    user-level one, so this edits the project-level file first (when
    *workspace* is given and defines *name*) — the file that actually
    determines the effective enabled state — falling back to the user-level
    file otherwise. Raises ``KeyError`` if the server is found in neither.
    """
    if workspace is not None and _try_set_enabled(
        _project_config_path(workspace), name, enabled=enabled
    ):
        return
    if _try_set_enabled(_user_config_path(home), name, enabled=enabled):
        return
    raise KeyError(name)


def load_mcp_config(home: Path, workspace: Path | None = None) -> McpConfig:
    """Load MCP server config, merging user-level and project-level files.

    Project-level (``.milky-frog/mcp.json``) overrides user-level
    (``~/.milky-frog/mcp.json``) for duplicate server names.
    """
    servers: dict[str, McpServerConfig] = {}
    candidates: list[Path] = [home / MCP_CONFIG_FILENAME]
    if workspace is not None:
        candidates.append(workspace / ".milky-frog" / MCP_CONFIG_FILENAME)

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg = McpConfig.model_validate(data)
            servers.update(cfg.mcpServers)
        except Exception as exc:
            logger.warning(
                "failed to load MCP config from %s; skipping (%s)",
                path,
                exc,
                exc_info=True,
            )

    return McpConfig(mcpServers=servers)
