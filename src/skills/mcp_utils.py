"""Utility functions for reading workspace/.mcp.json and building MCP metadata.

Single source of truth for MCP server discovery — replaces the old catalog
layer and enable_mcp_servers allowlist expansion. All callers (agent_sdk_cli,
generator) read the same .mcp.json file via these helpers.
"""

from __future__ import annotations

import json
import logging
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


