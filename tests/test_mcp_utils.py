"""Unit tests for src/mcp_utils.py."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.mcp_utils import (
    build_mcp_allowed_patterns,
    build_mcp_prompt_block,
    read_mcp_servers,
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


def test_build_allowed_patterns(tmp_path: Path) -> None:
    """build_mcp_allowed_patterns produces mcp__<name>__* list."""
    servers = {"github": {}, "notion": {}}
    patterns = build_mcp_allowed_patterns(servers)
    assert "mcp__github__*" in patterns
    assert "mcp__notion__*" in patterns
    assert len(patterns) == 2


def test_build_prompt_block_with_description(tmp_path: Path) -> None:
    """description field is used in the prompt block."""
    servers = {"github": {"description": "GitHub integration"}}
    block = build_mcp_prompt_block(servers)
    assert "GitHub integration" in block
    assert "mcp__github__*" in block
    assert "Доступні MCP сервери (1)" in block


def test_build_prompt_block_without_description(tmp_path: Path) -> None:
    """Falls back to server name when no description field."""
    servers = {"filesystem": {"type": "stdio", "command": "npx"}}
    block = build_mcp_prompt_block(servers)
    # server key name used as description fallback
    assert "filesystem" in block
    assert "mcp__filesystem__*" in block


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


def test_build_prompt_block_empty_servers() -> None:
    """Empty servers dict returns empty string."""
    assert build_mcp_prompt_block({}) == ""
