"""Tests for CapabilityIndex (US-001)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.agent.tools.capability_index import CapabilityIndex, IndexEntry, _tokenize


@dataclass
class _FakeSettings:
    skills_paths: list[str] = field(default_factory=list)


def _make_skill(parent: Path, name: str, description: str) -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nbody\n"
    )
    return skill_dir


@pytest.fixture(autouse=True)
def _reset_skills_singleton():
    import src.skills.agent_skills as agent_skills
    agent_skills._loader = None
    agent_skills._loader_paths = None
    yield
    agent_skills._loader = None
    agent_skills._loader_paths = None


@pytest.fixture
def empty_workspace(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def test_empty_index_returns_no_results(empty_workspace: Path):
    empty_workspace.mkdir()
    settings = _FakeSettings(skills_paths=[str(empty_workspace / "no-skills")])
    idx = CapabilityIndex(settings, empty_workspace)
    idx.build()
    # Built-in TOOLS still populate entries; query for unrelated junk should return []
    assert idx.query("xyzzy_no_match_token_anywhere") == []


def test_no_mcp_file_does_not_raise(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[str(tmp_path / "skills")])
    idx = CapabilityIndex(settings, workspace)
    idx.build()
    mcp_entries = [e for e in idx.entries if e.source == "mcp"]
    assert mcp_entries == []


def test_four_source_merge(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _make_skill(skills_root, "weather-skill", "Look up the weather forecast")

    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "github-mcp"], "description": "GitHub MCP server"},
        }
    }))

    from src.agent.tools.registry import register_dynamic_tool, unregister_dynamic_tool, Tool
    register_dynamic_tool(Tool(
        name="dyn_test_tool",
        sdk_name="DynTestTool",
        execution="direct",
        description="A dynamic test tool",
    ))

    try:
        settings = _FakeSettings(skills_paths=[str(skills_root)])
        idx = CapabilityIndex(settings, workspace)
        idx.build()

        sources = {e.source for e in idx.entries}
        assert "builtin" in sources
        assert "dynamic" in sources
        assert "skill" in sources
        assert "mcp" in sources
        assert any(e.source == "skill" and e.name == "weather-skill" for e in idx.entries)
        assert any(e.source == "mcp" and e.name == "github" for e in idx.entries)
    finally:
        unregister_dynamic_tool("dyn_test_tool")


def test_token_query_finds_skill_by_description(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _make_skill(skills_root, "weather-skill", "Look up the weather forecast")

    settings = _FakeSettings(skills_paths=[str(skills_root)])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    res = idx.query("weather", top_k=3)
    assert any(e.name == "weather-skill" for e in res)

    res2 = idx.query("forecast", top_k=3)
    assert any(e.name == "weather-skill" for e in res2)


def test_substring_fallback_for_zero_token_hits(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _make_skill(skills_root, "weather-skill", "Look up the weather forecast")

    settings = _FakeSettings(skills_paths=[str(skills_root)])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    # "weat" is not a token but appears as substring of "weather"
    res = idx.query("weat", top_k=3)
    assert any(e.name == "weather-skill" for e in res)


def test_top_k_ordering_by_popularity_then_source(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    # Manually craft entries with same score to test tie-breaker
    idx._entries = [
        IndexEntry(source="builtin", name="alpha", description="frobnicate widgets"),
        IndexEntry(source="skill", name="beta", description="frobnicate widgets"),
        IndexEntry(source="dynamic", name="gamma", description="frobnicate widgets", popularity_score=9.9),
        IndexEntry(source="mcp", name="delta", description="frobnicate widgets"),
    ]
    idx._inverted = {}
    for i, e in enumerate(idx._entries):
        for tok in _tokenize(f"{e.name} {e.description}"):
            idx._inverted.setdefault(tok, set()).add(i)

    res = idx.query("frobnicate", top_k=4)
    names = [e.name for e in res]
    # gamma (popularity wins), then skill > mcp > builtin
    assert names[0] == "gamma"
    assert names[1] == "beta"  # skill
    assert names[2] == "delta"  # mcp
    assert names[3] == "alpha"  # builtin


def test_build_performance_under_50ms(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    for i in range(100):
        _make_skill(skills_root, f"skill-{i:03d}", f"Synthetic skill number {i} doing thing {i}")

    mcp_servers = {f"server-{i}": {"description": f"MCP server {i}"} for i in range(10)}
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": mcp_servers}))

    from src.agent.tools.registry import register_dynamic_tool, unregister_dynamic_tool, Tool
    dyn_names = []
    for i in range(50):
        nm = f"dyn_perf_{i}"
        dyn_names.append(nm)
        register_dynamic_tool(Tool(
            name=nm,
            sdk_name=f"DynPerf{i}",
            execution="direct",
            description=f"Dynamic perf tool {i}",
        ))

    try:
        settings = _FakeSettings(skills_paths=[str(skills_root)])
        idx = CapabilityIndex(settings, workspace)

        start = time.perf_counter()
        idx.build()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert elapsed_ms < 50.0, f"build took {elapsed_ms:.2f}ms (target <50ms)"
        assert sum(1 for e in idx.entries if e.source == "skill") == 100
        assert sum(1 for e in idx.entries if e.source == "mcp") == 10
    finally:
        for nm in dyn_names:
            unregister_dynamic_tool(nm)


def test_query_p99_under_10ms(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    for i in range(100):
        _make_skill(skills_root, f"skill-{i:03d}", f"Synthetic weather forecast tool number {i}")

    mcp_servers = {f"server-{i}": {"description": f"MCP server {i}"} for i in range(10)}
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": mcp_servers}))

    settings = _FakeSettings(skills_paths=[str(skills_root)])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    timings_ms: list[float] = []
    for _ in range(100):
        start = time.perf_counter()
        idx.query("weather", top_k=3)
        timings_ms.append((time.perf_counter() - start) * 1000.0)

    timings_ms.sort()
    p99 = timings_ms[int(0.99 * len(timings_ms)) - 1]
    assert p99 < 10.0, f"p99 query was {p99:.2f}ms (target <10ms)"
