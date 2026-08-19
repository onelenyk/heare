"""Unit tests for src/mcp_utils.py."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from src.skills.mcp_utils import (
    read_mcp_servers,
    write_mcp_servers,
)


def _write_mcp(tmp_path: Path, data: object) -> Path:
    """Write .mcp.json and return workspace dir."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(json.dumps(data))
    return workspace


def test_read_mcp_servers_empty(tmp_path: Path) -> None:
    """Empty mcpServers dict returns empty dict."""
    workspace = _write_mcp(tmp_path, {"mcpServers": {}})
    result = read_mcp_servers(workspace)
    assert result == {}


def test_read_mcp_servers_populated(tmp_path: Path) -> None:
    """Populated mcpServers dict returns correct server names."""
    workspace = _write_mcp(
        tmp_path,
        {
            "mcpServers": {
                "github": {"type": "stdio", "command": "npx", "args": ["-y", "github-mcp"]},
                "filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "fs-mcp"]},
            }
        },
    )
    result = read_mcp_servers(workspace)
    assert set(result.keys()) == {"github", "filesystem"}


def test_read_mcp_servers_missing_file(tmp_path: Path) -> None:
    """Missing .mcp.json returns empty dict (no error)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = read_mcp_servers(workspace)
    assert result == {}


def test_read_mcp_servers_malformed_json(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed JSON returns empty dict and logs a WARNING."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text("this is not json {{{")

    with caplog.at_level(logging.WARNING, logger="heare.mcp_utils"):
        result = read_mcp_servers(workspace)

    assert result == {}
    assert any("Failed to parse" in r.message for r in caplog.records)


def test_read_mcp_servers_missing_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """.mcp.json without mcpServers key returns empty dict and logs a WARNING."""
    workspace = _write_mcp(tmp_path, {"someOtherKey": {}})

    with caplog.at_level(logging.WARNING, logger="heare.mcp_utils"):
        result = read_mcp_servers(workspace)

    assert result == {}
    assert any("mcpServers" in r.message for r in caplog.records)


def test_read_mcp_servers_wrong_types(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """mcpServers that is not a dict returns empty dict and logs a WARNING."""
    workspace = _write_mcp(tmp_path, {"mcpServers": ["github", "notion"]})

    with caplog.at_level(logging.WARNING, logger="heare.mcp_utils"):
        result = read_mcp_servers(workspace)

    assert result == {}
    assert any("mcpServers" in r.message for r in caplog.records)


# --- write_mcp_servers tests ---


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    """write_mcp_servers followed by read_mcp_servers returns equivalent data."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    servers = {
        "github": {"type": "stdio", "command": "npx", "args": ["-y", "github-mcp"]},
        "filesystem": {"type": "stdio", "command": "npx", "args": ["-y", "fs-mcp"]},
    }
    write_mcp_servers(workspace, servers)
    result = read_mcp_servers(workspace)
    assert result == servers


def test_write_creates_workspace_dir_if_missing(tmp_path: Path) -> None:
    """write_mcp_servers creates workspace_dir when it does not exist."""
    workspace = tmp_path / "new" / "nested" / "workspace"
    assert not workspace.exists()
    write_mcp_servers(workspace, {"myserver": {"type": "stdio", "command": "npx"}})
    assert (workspace / ".mcp.json").exists()


def test_write_is_atomic_no_partial_file_on_crash(tmp_path: Path) -> None:
    """Crash during os.replace leaves .mcp.json unchanged (original content preserved)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = {"mcpServers": {"original": {"type": "stdio", "command": "original-cmd"}}}
    (workspace / ".mcp.json").write_text(json.dumps(original))

    new_servers = {"replaced": {"type": "stdio", "command": "new-cmd"}}
    with patch("os.replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            write_mcp_servers(workspace, new_servers)

    result = json.loads((workspace / ".mcp.json").read_text())
    assert result == original


def test_write_empty_list(tmp_path: Path) -> None:
    """write_mcp_servers with empty dict writes {\"mcpServers\": {}} without deleting the file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_mcp_servers(workspace, {})
    assert (workspace / ".mcp.json").exists()
    result = json.loads((workspace / ".mcp.json").read_text())
    assert result == {"mcpServers": {}}


def test_round_trip_preserves_unicode(tmp_path: Path) -> None:
    """Server names and values with UTF-8 characters survive a write/read roundtrip."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    servers = {"сервер": {"type": "stdio", "command": "npx", "description": "Кирилиця"}}
    write_mcp_servers(workspace, servers)
    result = read_mcp_servers(workspace)
    assert result == servers
