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


# Per-server, session-scoped debounce sets for indication notifications.
# Cleared implicitly on daemon restart (in-process state only).
_AUTH_NOTIFIED: set[str] = set()
_DISCONNECT_NOTIFIED: set[str] = set()


def notify_mcp_auth_required(server_name: str) -> bool:
    """Fire MCP_AUTH_REQUIRED for `server_name` once per daemon lifetime.

    Returns True if a fresh notification was emitted, False if debounced.
    """
    if server_name in _AUTH_NOTIFIED:
        return False
    _AUTH_NOTIFIED.add(server_name)
    try:
        from src.voice.indication.core import IndicationKind, get_indication

        ind = get_indication()
        if ind is not None:
            ind.notify(
                IndicationKind.MCP_AUTH_REQUIRED,
                body=f"MCP server '{server_name}' needs authentication — check terminal",
            )
    except Exception:
        logger.warning("indication notify for MCP auth failed", exc_info=True)
    return True


def notify_mcp_disconnected(server_name: str) -> bool:
    """Fire MCP_DISCONNECTED for `server_name` once per 30s per server.

    Implementation deliberately keeps the simpler "once per session" debounce
    inline; a 30s window can be added if the call site grows hot.
    """
    if server_name in _DISCONNECT_NOTIFIED:
        return False
    _DISCONNECT_NOTIFIED.add(server_name)
    try:
        from src.voice.indication.core import IndicationKind, get_indication

        ind = get_indication()
        if ind is not None:
            ind.notify(
                IndicationKind.MCP_DISCONNECTED,
                body=f"MCP server '{server_name}' disconnected",
            )
    except Exception:
        logger.warning("indication notify for MCP disconnect failed", exc_info=True)
    return True


def reset_mcp_indication_debounce() -> None:
    """Clear per-server debounce sets — call between sessions in tests."""
    _AUTH_NOTIFIED.clear()
    _DISCONNECT_NOTIFIED.clear()


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


def build_mcp_allowed_patterns(servers: dict[str, dict]) -> list[str]:
    """Return mcp__<name>__* wildcard patterns for each server in the dict."""
    return [f"mcp__{name}__*" for name in servers]


def build_mcp_prompt_block(servers: dict[str, dict]) -> str:
    """Return a Ukrainian-language description block of available MCP servers.

    Each entry uses the optional 'description' field from the .mcp.json entry,
    falling back to the server key name. Returns empty string when no servers.
    """
    if not servers:
        return ""

    lines = [f"Доступні MCP сервери ({len(servers)}):"]
    for name, entry in servers.items():
        desc = entry.get("description", name) if isinstance(entry, dict) else name
        lines.append(f"  - {name}: {desc} (інструменти: mcp__{name}__*)")

    return "\n".join(lines)
