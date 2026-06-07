"""Workspace directory helpers.

Kept separate from main.py so non-CLI modules can import without circular deps.
can call ``ensure_workspace_mcp`` without pulling the CLI dispatcher.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger("heare.workspace")


def ensure_workspace_mcp(workspace_dir: Path) -> None:
    """Seed an empty ``workspace/.mcp.json`` on first run.

    Idempotent — no-ops when the target file already exists. The user can
    edit the file afterward to add or remove MCP servers.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    target = workspace_dir / ".mcp.json"
    if target.exists():
        return
    target.write_text(json.dumps({"mcpServers": {}}, indent=2))
    logger.info("seeded empty %s", target)
