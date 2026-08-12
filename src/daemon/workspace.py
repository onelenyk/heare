"""Workspace directory helpers.

Kept separate from main.py so non-CLI modules can import without circular deps.
can call ``ensure_mcp_config`` without pulling the CLI dispatcher.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger("heare.workspace")

# The conventional filename, kept so a config from elsewhere can be
# dropped in unchanged. What moved is the directory: the workspace is
# where `bash` runs and where `write` resolves paths, and an MCP entry is
# a command line executed at every daemon start.
MCP_FILE = ".mcp.json"


def ensure_mcp_config(mcp_dir: Path, legacy_workspace_dir: Path | None = None) -> Path:
    """Seed ``<mcp_dir>/.mcp.json``, moving an older one out of the workspace.

    Idempotent — no-ops once the target exists. Returns the target path.

    The migration runs once and renames the old file rather than copying
    it, so nothing is left behind that still looks authoritative to a
    person reading the workspace.
    """
    mcp_dir.mkdir(parents=True, exist_ok=True)
    target = mcp_dir / MCP_FILE
    if target.exists():
        return target

    legacy = (legacy_workspace_dir / MCP_FILE) if legacy_workspace_dir else None
    if legacy is not None and legacy.exists():
        try:
            data = json.loads(legacy.read_text())
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            if isinstance(servers, dict) and servers:
                target.write_text(json.dumps({"mcpServers": servers}, indent=2))
                legacy.rename(legacy.with_suffix(".json.moved"))
                logger.warning(
                    "moved %d MCP server(s) out of the workspace into %s — "
                    "the workspace copy is no longer read",
                    len(servers),
                    target,
                )
                return target
            legacy.rename(legacy.with_suffix(".json.moved"))
            logger.info("retired an empty %s", legacy)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("could not migrate %s: %s", legacy, exc)

    target.write_text(json.dumps({"mcpServers": {}}, indent=2))
    logger.info("seeded empty %s", target)
    return target
