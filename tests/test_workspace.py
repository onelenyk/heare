"""Tests for ``src/daemon/workspace.py`` ``ensure_workspace_mcp``."""
from __future__ import annotations

import json
from pathlib import Path

from src.daemon.workspace import ensure_workspace_mcp


def test_ensure_workspace_mcp_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Second call with same dir must not overwrite existing file."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / ".mcp.json"
    original_content = {"mcpServers": {"my-server": {"command": "custom"}}}
    target.write_text(json.dumps(original_content))

    # Call twice — second call must be a no-op
    ensure_workspace_mcp(workspace)
    ensure_workspace_mcp(workspace)

    assert json.loads(target.read_text()) == original_content


def test_seeds_from_global_claude_config(tmp_path: Path, monkeypatch) -> None:
    """When ~/.claude.json has mcpServers, seed them into workspace."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    (fake_home / ".claude.json").write_text(json.dumps({
        "mcpServers": {
            "android-adb": {"command": "npx", "args": ["@foo/server"]},
            "mobile": {"command": "npx", "args": ["-y", "@bar/server"]},
        },
        "other_field": "ignored",
    }))

    workspace = tmp_path / "workspace"
    ensure_workspace_mcp(workspace)

    seeded = json.loads((workspace / ".mcp.json").read_text())
    assert "mcpServers" in seeded
    assert set(seeded["mcpServers"].keys()) == {"android-adb", "mobile"}
    assert "other_field" not in seeded


def test_handles_missing_global_config(tmp_path: Path, monkeypatch) -> None:
    """No ~/.claude.json → resulting file is {"mcpServers": {}}."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    # Deliberately no .claude.json written

    workspace = tmp_path / "workspace"
    ensure_workspace_mcp(workspace)

    assert json.loads((workspace / ".mcp.json").read_text()) == {"mcpServers": {}}


def test_corrupt_global_config_falls_back_to_empty(tmp_path: Path, monkeypatch) -> None:
    """A broken ~/.claude.json must not crash — seed empty servers."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    (fake_home / ".claude.json").write_text("{not valid json")

    workspace = tmp_path / "workspace"
    ensure_workspace_mcp(workspace)

    assert json.loads((workspace / ".mcp.json").read_text()) == {"mcpServers": {}}


def test_creates_workspace_dir_if_missing(tmp_path: Path, monkeypatch) -> None:
    """Parent dir doesn't exist yet → mkdir(parents=True) is called."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    workspace = tmp_path / "deep" / "nested" / "workspace"
    ensure_workspace_mcp(workspace)
    assert workspace.is_dir()
    assert (workspace / ".mcp.json").exists()
