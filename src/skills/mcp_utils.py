"""Utility functions for reading workspace/.mcp.json and building MCP metadata.

Single source of truth for MCP server discovery — replaces the old catalog
layer and enable_mcp_servers allowlist expansion. All callers (agent_sdk_cli,
generator) read the same .mcp.json file via these helpers.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("heare.mcp_utils")


def read_mcp_servers(workspace_dir: Path) -> dict[str, dict]:
    """Read workspace_dir/.mcp.json and return the mcpServers dict.

    Gracefully handles: missing file, invalid JSON, missing mcpServers key,
    wrong types — returns empty dict and logs a WARNING in all error cases.
    """
    mcp_file = workspace_dir / ".mcp.json"
    if not mcp_file.exists():
        return {}

    try:
        data = json.loads(mcp_file.read_text())
    except json.JSONDecodeError as exc:
        logger.warning(
            "Failed to parse %s: %s — treating as no MCP servers", mcp_file, exc
        )
        return {}
    except OSError as exc:
        logger.warning("Cannot read %s: %s — treating as no MCP servers", mcp_file, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "%s top-level value is %s, expected dict — treating as no MCP servers",
            mcp_file,
            type(data).__name__,
        )
        return {}

    servers = data.get("mcpServers")
    if servers is None:
        logger.warning(
            "%s has no 'mcpServers' key — treating as no MCP servers", mcp_file
        )
        return {}

    if not isinstance(servers, dict):
        logger.warning(
            "%s 'mcpServers' is %s, expected dict — treating as no MCP servers",
            mcp_file,
            type(servers).__name__,
        )
        return {}

    return servers


def write_mcp_servers(workspace_dir: Path, servers: dict[str, dict]) -> None:
    """Atomic-write the MCP server list to workspace_dir/.mcp.json.

    Single source of truth for .mcp.json writes. Must be called by anything
    that mutates the MCP config (currently the planned installer).

    Atomicity: write to a temp file in the same directory, then os.replace().
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".mcp.", suffix=".json.tmp", dir=str(workspace_dir)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"mcpServers": servers}, f, indent=2)
        os.replace(tmp_name, workspace_dir / ".mcp.json")
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
