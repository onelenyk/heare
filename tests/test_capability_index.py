"""Tests for CapabilityIndex (US-001)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.agent.tools.capability_index import (
    CapabilityIndex,
    IndexEntry,
    _MCP_TOOLS_PER_SERVER_CAP,
    _tokenize,
)


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


def _register_mcp_tools(specs: list[tuple[str, str]]) -> None:
    """Append fake live MCP ToolDefs, mirroring what mcp_bridge.py does.

    ``specs`` is a list of (fn_name, description) pairs where fn_name
    already follows the ``mcp__<slug>__<tool>`` shape.
    """
    from src.agent.tools.system import TOOLS as _SYSTEM_TOOLS, ToolDef

    for fn_name, description in specs:
        _SYSTEM_TOOLS.append(
            ToolDef(
                name=fn_name,
                description=description,
                handler="mcp",
                schema_fields={},
                required=[],
            )
        )


def _clear_mcp_tools() -> None:
    from src.agent.tools.system import TOOLS as _SYSTEM_TOOLS

    _SYSTEM_TOOLS[:] = [t for t in _SYSTEM_TOOLS if not t.name.startswith("mcp__")]


@pytest.fixture(autouse=True)
def _reset_skills_singleton():
    import src.skills.agent_skills as agent_skills
    agent_skills._loader = None
    agent_skills._loader_paths = None
    yield
    agent_skills._loader = None
    agent_skills._loader_paths = None


@pytest.fixture(autouse=True)
def _reset_mcp_live_tools():
    """Safety net: src.agent.tools.system.TOOLS is a shared module-level
    list. A test that appends mcp__ entries and then fails before its
    finally/cleanup would leak them into every later test in the process.
    """
    _clear_mcp_tools()
    yield
    _clear_mcp_tools()


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


def test_ukrainian_intent_finds_builtin_tools(tmp_path: Path):
    """A spoken Ukrainian intent must match English-named builtin tools.

    Before the fix the tokenizer was [a-z0-9_] — every Cyrillic intent
    became zero tokens and the index could never match anything said aloud.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    res = idx.query("подивись мої вкладки в браузері", top_k=5)
    assert any(e.name == "list_browser_tabs" for e in res), [e.name for e in res]

    res2 = idx.query("загугли останні новини", top_k=5)
    assert any(e.name == "web_search" for e in res2), [e.name for e in res2]

    res3 = idx.query("запам'ятай що я люблю каву", top_k=5)
    assert any(e.name == "remember" for e in res3), [e.name for e in res3]


def test_ukrainian_inflection_still_matches(tmp_path: Path):
    """«вкладку», «вкладок» are the same word as «вкладки» to the index."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    for form in ("вкладку", "вкладок", "вкладки"):
        res = idx.query(f"відкрий нову {form}", top_k=5)
        assert any("browser" in e.name for e in res), (form, [e.name for e in res])


def test_ukrainian_stopwords_do_not_score(tmp_path: Path):
    """Pure function words must not produce matches."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    assert idx.query("будь ласка і не за про") == []


def test_keywords_field_is_indexed_but_not_required():
    idx = CapabilityIndex(_FakeSettings(skills_paths=[]), Path("/nonexistent"))
    idx._entries = [
        IndexEntry(source="skill", name="tab-helper",
                   description="Manage browser tabs", keywords="вкладки браузер"),
        IndexEntry(source="skill", name="other", description="Something else"),
    ]
    idx._inverted = {}
    from src.agent.tools.capability_index import _stem
    for i, e in enumerate(idx._entries):
        for tok in _tokenize(f"{e.name} {e.description} {e.keywords}"):
            idx._inverted.setdefault(_stem(tok), set()).add(i)

    res = idx.query("покажи вкладки", top_k=2)
    assert [e.name for e in res] == ["tab-helper"]


def test_mcp_keywords_read_from_json(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "github": {
                "command": "npx", "args": ["-y", "github-mcp"],
                "description": "GitHub MCP server",
                "keywords": "гітхаб репозиторій пулреквест",
            },
        }
    }))
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    idx.build()

    res = idx.query("подивись мій гітхаб", top_k=3)
    assert any(e.name == "github" for e in res), [e.name for e in res]


def test_mcp_individual_tools_discovered_in_ukrainian(tmp_path: Path):
    """Individual MCP tools (not just the server) must be findable in
    Ukrainian even though their name/description are English, sourced from
    the live worker registry (src.agent.tools.system.TOOLS)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "fs-mcp"],
                       "description": "Filesystem MCP server"},
        }
    }))
    _register_mcp_tools([
        ("mcp__files__list_directory",
         "Get a detailed listing of all files and directories in a path"),
        ("mcp__files__write_file",
         "Create a new file or overwrite an existing file with content"),
    ])
    try:
        settings = _FakeSettings(skills_paths=[])
        idx = CapabilityIndex(settings, workspace)
        idx.build()

        res = idx.query("покажи файли в теці", top_k=5)
        assert any(e.name == "mcp__files__list_directory" for e in res), [e.name for e in res]

        res2 = idx.query("запиши у файл", top_k=5)
        assert any(e.name == "mcp__files__write_file" for e in res2), [e.name for e in res2]
    finally:
        _clear_mcp_tools()


def test_mcp_per_server_entry_still_matches_alongside_tools(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "fs-mcp"],
                       "description": "Filesystem MCP server",
                       "keywords": "файли тека"},
        }
    }))
    _register_mcp_tools([
        ("mcp__files__list_directory", "Get a detailed listing of files"),
    ])
    try:
        settings = _FakeSettings(skills_paths=[])
        idx = CapabilityIndex(settings, workspace)
        idx.build()

        res = idx.query("покажи файли в теці", top_k=5)
        names = [e.name for e in res]
        assert "files" in names, names  # coarse per-server entry
        assert "mcp__files__list_directory" in names, names  # precise tool entry
    finally:
        _clear_mcp_tools()


def test_mcp_tools_capped_per_server(tmp_path: Path):
    """One query must not be dominated by every tool of a single chatty
    server; at most _MCP_TOOLS_PER_SERVER_CAP individual tool entries from
    the same server may appear in one result set."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "fs-mcp"],
                       "description": "Filesystem MCP server"},
        }
    }))
    _register_mcp_tools([
        ("mcp__files__list_directory", "List files in a directory"),
        ("mcp__files__read_file", "Read a file"),
        ("mcp__files__write_file", "Write a file"),
        ("mcp__files__delete_file", "Delete a file"),
        ("mcp__files__move_file", "Move a file"),
        ("mcp__files__create_directory", "Create a directory"),
    ])
    try:
        settings = _FakeSettings(skills_paths=[])
        idx = CapabilityIndex(settings, workspace)
        idx.build()

        res = idx.query("файл", top_k=10)
        file_tool_names = [e.name for e in res if e.name.startswith("mcp__files__")]
        assert len(file_tool_names) <= _MCP_TOOLS_PER_SERVER_CAP, file_tool_names
    finally:
        _clear_mcp_tools()


def test_index_builds_with_zero_mcp_tools_connected(tmp_path: Path):
    """No live MCP connection (nothing in src.agent.tools.system.TOOLS
    named mcp__*) must not break the build — the per-tool source is
    optional and additive."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = _FakeSettings(skills_paths=[])
    idx = CapabilityIndex(settings, workspace)
    idx.build()  # must not raise
    assert not any(e.dedupe_group for e in idx.entries)


def test_install_tools_hidden_when_gate_closed(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    idx = CapabilityIndex(_FakeSettings(skills_paths=[]), workspace)
    idx.build()
    names = {e.name for e in idx.entries}
    assert "install_skill_tool" not in names
    assert "register_mcp_server" not in names

    @dataclass
    class _OpenSettings(_FakeSettings):
        capability_install_enabled: bool = True

    idx_open = CapabilityIndex(_OpenSettings(skills_paths=[]), workspace)
    idx_open.build()
    open_names = {e.name for e in idx_open.entries}
    assert "install_skill_tool" in open_names
    assert "register_mcp_server" in open_names


def test_build_performance_under_50ms(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    for i in range(100):
        _make_skill(skills_root, f"skill-{i:03d}", f"Synthetic skill number {i} doing thing {i}")

    mcp_servers = {f"server-{i}": {"description": f"MCP server {i}"} for i in range(10)}
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": mcp_servers}))

    # 50 individual MCP tools (5 per server, 10 servers) — the live worker
    # registry is what the index now also reads for MCP.
    mcp_tool_specs = [
        (f"mcp__server-{s}__tool_{t}", f"Synthetic MCP tool {t} of server {s}")
        for s in range(10)
        for t in range(5)
    ]
    _register_mcp_tools(mcp_tool_specs)

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
        # 10 per-server summary entries + 50 individual tool entries
        assert sum(1 for e in idx.entries if e.source == "mcp") == 60
    finally:
        for nm in dyn_names:
            unregister_dynamic_tool(nm)
        _clear_mcp_tools()


def test_query_p99_under_10ms(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    for i in range(100):
        _make_skill(skills_root, f"skill-{i:03d}", f"Synthetic weather forecast tool number {i}")

    mcp_servers = {f"server-{i}": {"description": f"MCP server {i}"} for i in range(10)}
    (workspace / ".mcp.json").write_text(json.dumps({"mcpServers": mcp_servers}))

    mcp_tool_specs = [
        (f"mcp__server-{s}__tool_{t}", f"Synthetic MCP tool {t} of server {s}")
        for s in range(10)
        for t in range(5)
    ]
    _register_mcp_tools(mcp_tool_specs)

    try:
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
    finally:
        _clear_mcp_tools()
