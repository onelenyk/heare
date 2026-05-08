"""Workspace directory helpers.

Kept separate from main.py so non-CLI modules (onboarding, capabilities)
can call ``ensure_workspace_mcp`` without pulling the CLI dispatcher.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger("heare.workspace")


def ensure_workspace_mcp(workspace_dir: Path) -> None:
    """Seed ``workspace/.mcp.json`` from ``~/.claude.json`` on first run.

    Idempotent — no-ops when the target file already exists. The user can
    edit the seeded file afterward to add or remove servers.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    target = workspace_dir / ".mcp.json"
    if target.exists():
        return
    global_cfg = Path.home() / ".claude.json"
    mcp_servers: dict = {}
    if global_cfg.exists():
        try:
            data = json.loads(global_cfg.read_text())
            mcp_servers = data.get("mcpServers", {}) or {}
        except (OSError, json.JSONDecodeError):
            pass
    target.write_text(json.dumps({"mcpServers": mcp_servers}, indent=2))
    logger.info(
        "seeded %s with %d MCP server(s) from global config",
        target,
        len(mcp_servers),
    )
    if mcp_servers:
        names = ", ".join(mcp_servers.keys())
        logger.warning(
            "Auto-authorized MCP servers from ~/.claude.json: %s. "
            "All servers in workspace/.mcp.json are now callable by the agent. "
            "Review and remove any unwanted entries.",
            names,
        )
