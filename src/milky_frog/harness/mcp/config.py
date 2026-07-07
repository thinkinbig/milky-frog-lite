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

    mcpServers: dict[str, McpServerConfig] = {}


def _user_config_path(home: Path) -> Path:
    return home / MCP_CONFIG_FILENAME


def set_server_enabled(home: Path, name: str, *, enabled: bool) -> None:
    """Toggle a server's ``enabled`` flag in the user-level mcp.json.

    Raises ``KeyError`` if the server is not found.
    """
    path = _user_config_path(home)
    try:
        data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OSError(f"cannot read {path}: {exc}") from exc

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        raise KeyError(name)

    servers[name]["enabled"] = enabled  # type: ignore[index]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
