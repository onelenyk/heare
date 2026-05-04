"""Tests for the user-facing capability discovery tools (US-007).

Covers ``discover_capability``, ``install_skill_tool``, ``revoke_capability``,
and ``list_capabilities`` direct-tool handlers in ``src.direct_tools``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import direct_tools
from src.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    workspace_dir: Path | None = None
    skills_paths: list[str] = field(default_factory=list)
    speaker_id_enabled: bool = True
    confirmation_passphrase: str | None = None
    speakers_file: Path | None = None
    identity_file: Path = field(default_factory=lambda: Path("/nonexistent"))
    marketplace_url: str = ""
    mcp_registry_url: str = ""
    installation_signature_required: bool = False


def _make_index(entries: list[IndexEntry]):
    """Build a stub CapabilityIndex-like object with the given entries."""
    idx = MagicMock()
    idx.entries = entries
    idx.query = MagicMock(side_effect=lambda intent, top_k=3: entries[:top_k])
    idx.rebuild = MagicMock()
    return idx


@pytest.fixture(autouse=True)
def _reset_singleton():
    direct_tools._capability_index_singleton = None
    yield
    direct_tools._capability_index_singleton = None


@pytest.fixture
def settings(tmp_path: Path) -> _FakeSettings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _FakeSettings(workspace_dir=workspace)


# ---------------------------------------------------------------------------
# discover_capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_capability_returns_top_3(settings):
    entries = [
        IndexEntry(source="skill", name=f"skill-{i}", description=f"d{i}")
        for i in range(5)
    ]
    direct_tools.set_capability_index(_make_index(entries))

    result = await direct_tools._execute_discover_capability(
        json.dumps({"intent": "weather kyiv"}), settings
    )
    assert result["success"] is True
    assert len(result["results"]) == 3
    assert result["source"] == "local"


@pytest.mark.asyncio
async def test_discover_capability_falls_through_to_remote_on_local_miss(settings):
    direct_tools.set_capability_index(_make_index([]))
    remote = [IndexEntry(source="skill", name="weather-pro", description="weather")]

    with patch("src.discovery.discover_capability_remote", AsyncMock(return_value=remote)):
        result = await direct_tools._execute_discover_capability(
            json.dumps({"intent": "weather kyiv"}), settings
        )

    assert result["success"] is True
    assert result["source"] == "remote"
    assert result["results"][0]["name"] == "weather-pro"


@pytest.mark.asyncio
async def test_discover_capability_returns_refusal_when_empty(settings):
    direct_tools.set_capability_index(_make_index([]))
    with patch("src.discovery.discover_capability_remote", AsyncMock(return_value=[])):
        result = await direct_tools._execute_discover_capability(
            json.dumps({"intent": "weather kyiv"}), settings
        )

    assert result["success"] is True
    assert result["results"] == []
    assert "don't have a tool" in result["spoken"]["en"].lower()
    assert "не маю інструменту" in result["spoken"]["uk"].lower()


@pytest.mark.asyncio
async def test_discover_capability_invalid_args(settings):
    result = await direct_tools._execute_discover_capability("{not-json", settings)
    assert result["success"] is False
    assert "JSON" in result["error"]


@pytest.mark.asyncio
async def test_discover_capability_missing_intent(settings):
    result = await direct_tools._execute_discover_capability(json.dumps({}), settings)
    assert result["success"] is False


# ---------------------------------------------------------------------------
# install_skill_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_skill_tool_dispatch(settings):
    entry = IndexEntry(
        source="skill",
        name="weather-pro",
        description="weather",
        install_url="https://github.com/foo/weather-pro/archive/v1.tar.gz",
    )
    direct_tools.set_capability_index(_make_index([entry]))

    fake_result = MagicMock()
    fake_result.success = True
    fake_result.slug = "weather-pro"
    fake_result.message_en = "Installed weather-pro."
    fake_result.message_uk = "Встановив weather-pro."
    fake_result.requires_restart = False
    fake_result.error_code = None

    fake_install = AsyncMock(return_value=fake_result)
    with patch("src.installer.install_skill", fake_install):
        result = await direct_tools._execute_install_skill_tool(
            json.dumps({"slug": "weather-pro", "user_confirmed": True}),
            settings,
        )

    assert result["success"] is True
    fake_install.assert_called_once()
    call_kwargs = fake_install.call_args.kwargs
    assert call_kwargs["user_confirmed"] is True
    assert call_kwargs["replace"] is False
    assert call_kwargs["settings"] is settings


@pytest.mark.asyncio
async def test_install_skill_tool_unknown_slug(settings):
    direct_tools.set_capability_index(_make_index([]))
    result = await direct_tools._execute_install_skill_tool(
        json.dumps({"slug": "nonexistent", "user_confirmed": True}), settings
    )
    assert result["success"] is False
    assert "Unknown" in result["error"]


# ---------------------------------------------------------------------------
# revoke_capability
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(direct_tools.Path, "home", classmethod(lambda cls: home))
    return home


@pytest.mark.asyncio
async def test_revoke_capability_refuses_without_sidecar(settings, fake_home):
    skill_dir = fake_home / ".heare" / "skills" / "_marketplace" / "user-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: user-skill\ndescription: x\n---\n")

    result = await direct_tools._execute_revoke_capability(
        json.dumps({"slug": "user-skill"}), settings
    )
    assert result["success"] is False
    assert "user_authored_skill_protected" == result["error"]
    assert skill_dir.exists()  # not deleted


@pytest.mark.asyncio
async def test_revoke_capability_succeeds_with_sidecar(settings, fake_home):
    skill_dir = fake_home / ".heare" / "skills" / "_marketplace" / "weather-pro"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: weather-pro\ndescription: x\n---\n")
    (skill_dir / ".install.json").write_text("{}")

    direct_tools.set_capability_index(_make_index([]))

    result = await direct_tools._execute_revoke_capability(
        json.dumps({"slug": "weather-pro"}), settings
    )
    assert result["success"] is True
    assert not skill_dir.exists()


@pytest.mark.asyncio
async def test_revoke_capability_invalidates_loader_and_rebuilds_index(settings, fake_home):
    skill_dir = fake_home / ".heare" / "skills" / "_marketplace" / "weather-pro"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: weather-pro\ndescription: x\n---\n")
    (skill_dir / ".install.json").write_text("{}")

    fake_loader = MagicMock()
    fake_index = _make_index([])
    direct_tools.set_capability_index(fake_index)

    with patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await direct_tools._execute_revoke_capability(
            json.dumps({"slug": "weather-pro"}), settings
        )

    assert result["success"] is True
    fake_loader.invalidate.assert_called_once()
    fake_index.rebuild.assert_called_once()


@pytest.mark.asyncio
async def test_revoke_capability_not_found(settings, fake_home):
    result = await direct_tools._execute_revoke_capability(
        json.dumps({"slug": "missing"}), settings
    )
    assert result["success"] is False
    assert result["error"] == "not_found"


# ---------------------------------------------------------------------------
# list_capabilities
# ---------------------------------------------------------------------------


def _stub_skill_meta(name: str, *, installed_via_discovery: bool = True):
    m = MagicMock()
    m.name = name
    m.description = f"desc-{name}"
    m.installed_via_discovery = installed_via_discovery
    return m


@pytest.mark.asyncio
async def test_list_capabilities_returns_three_buckets(settings):
    """The new contract returns explicit ``built_in``/``skills``/``mcps``
    categories so the LLM can answer 'what can you do' without guessing."""
    skills = [_stub_skill_meta(f"sk-{i}") for i in range(3)]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value={}),
    ):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert "totals" in result
    assert "categories" in result
    assert set(result["categories"].keys()) == {"built_in", "skills", "mcps"}
    # Built-in tools always come from tool_registry — non-empty in practice.
    assert result["totals"]["built_in"] > 0
    assert result["totals"]["skills"] == 3
    assert result["totals"]["mcps"] == 0
    assert result["count"] == result["totals"]["all"]


@pytest.mark.asyncio
async def test_list_capabilities_summary_flag_when_total_above_threshold(settings):
    """``summary=True`` flips when the flat total exceeds the threshold so
    the LLM can ask before reading the full list aloud."""
    skills = [_stub_skill_meta(f"sk-{i}") for i in range(20)]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value={}),
    ):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert result["summary"] is True
    assert result["count"] >= 20


@pytest.mark.asyncio
async def test_list_capabilities_includes_user_authored_skills_with_flag(settings):
    """User-authored and discovery-installed skills both surface; the
    ``installed_via_discovery`` flag distinguishes them so the LLM can
    explain provenance if asked."""
    skills = [
        _stub_skill_meta("user-skill", installed_via_discovery=False),
        _stub_skill_meta("market-skill", installed_via_discovery=True),
    ]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value={}),
    ):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    skill_rows = result["categories"]["skills"]
    by_name = {r["name"]: r for r in skill_rows}
    assert by_name["user-skill"]["installed_via_discovery"] is False
    assert by_name["market-skill"]["installed_via_discovery"] is True


@pytest.mark.asyncio
async def test_list_capabilities_lists_mcps_from_workspace_config(settings):
    """MCPs come from the workspace ``.mcp.json`` — every server visible
    to the daemon must appear, including ones marked ``disabled``."""
    fake_loader = MagicMock()
    fake_loader.discover.return_value = []
    servers = {
        "android-adb": {"command": "npx", "args": ["@x/adb"], "disabled": True},
        "mobile": {"command": "npx", "args": ["-y", "@m/mobile"]},
    }
    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value=servers),
    ):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    mcp_rows = result["categories"]["mcps"]
    names = {r["name"] for r in mcp_rows}
    assert names == {"android-adb", "mobile"}
    adb = next(r for r in mcp_rows if r["name"] == "android-adb")
    assert adb["disabled"] is True
    assert "(disabled)" in adb["description"]


@pytest.mark.asyncio
async def test_list_capabilities_category_filter(settings):
    """``category`` filter scopes the result to one bucket and accepts
    friendly aliases (``tools`` → built_in, ``mcp`` → mcps)."""
    fake_loader = MagicMock()
    fake_loader.discover.return_value = [_stub_skill_meta("sk-a")]
    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value={"foo": {}}),
    ):
        only_skills = await direct_tools._execute_list_capabilities(
            json.dumps({"category": "skills"}), settings
        )
        only_mcps = await direct_tools._execute_list_capabilities(
            json.dumps({"category": "mcp"}), settings
        )
        only_built = await direct_tools._execute_list_capabilities(
            json.dumps({"category": "tools"}), settings
        )

    assert only_skills["totals"]["built_in"] == 0
    assert only_skills["totals"]["mcps"] == 0
    assert only_skills["totals"]["skills"] == 1

    assert only_mcps["totals"]["mcps"] == 1
    assert only_mcps["totals"]["skills"] == 0
    assert only_mcps["totals"]["built_in"] == 0

    assert only_built["totals"]["built_in"] > 0
    assert only_built["totals"]["skills"] == 0
    assert only_built["totals"]["mcps"] == 0


@pytest.mark.asyncio
async def test_list_capabilities_empty_skills_and_mcps_still_lists_built_in(settings):
    """Even with zero skills and zero MCPs, the response must surface the
    built-in tool registry — those are the real 'what can you do' answer
    on a fresh install."""
    fake_loader = MagicMock()
    fake_loader.discover.return_value = []
    with (
        patch("src.agent_skills.get_skills_loader", return_value=fake_loader),
        patch("src.mcp_utils.read_mcp_servers", return_value={}),
    ):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert result["totals"]["built_in"] > 0
    assert result["totals"]["skills"] == 0
    assert result["totals"]["mcps"] == 0
    # The flat ``items`` list still exists for backwards compat.
    assert any(i["source"] == "built_in" for i in result["items"])


# ---------------------------------------------------------------------------
# Tool registration smoke tests
# ---------------------------------------------------------------------------


def test_capability_tools_registered():
    from src.tool_registry import TOOLS

    for name in (
        "discover_capability",
        "install_skill_tool",
        "install_mcp_server_tool",
        "revoke_capability",
        "list_capabilities",
    ):
        assert name in TOOLS, f"{name} missing from TOOLS"
        assert TOOLS[name].enabled is True
        assert TOOLS[name].execution == "direct"


def test_capability_tools_have_schemas():
    from src.llm_tools import _TOOL_SPECS

    for name in (
        "discover_capability",
        "install_skill_tool",
        "install_mcp_server_tool",
        "revoke_capability",
        "list_capabilities",
    ):
        assert name in _TOOL_SPECS, f"{name} missing from _TOOL_SPECS"
        properties, required, serializer = _TOOL_SPECS[name]
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        assert callable(serializer)


# ---------------------------------------------------------------------------
# US-003: register_mcp_server (direct install from user-supplied launch info)
# ---------------------------------------------------------------------------


def test_register_mcp_server_registered():
    from src.tool_registry import TOOLS
    from src.llm_tools import _TOOL_SPECS

    assert "register_mcp_server" in TOOLS
    assert TOOLS["register_mcp_server"].enabled is True
    assert TOOLS["register_mcp_server"].execution == "direct"

    assert "register_mcp_server" in _TOOL_SPECS
    properties, required, _ = _TOOL_SPECS["register_mcp_server"]
    assert {"slug", "description", "command", "args", "user_confirmed"} <= set(required)
    assert "env" in properties
    assert "source_url" in properties


def _consent_settings(workspace: Path) -> _FakeSettings:
    return _FakeSettings(
        workspace_dir=workspace,
        speaker_id_enabled=False,
        confirmation_passphrase="open sesame",
    )


@pytest.mark.asyncio
async def test_register_mcp_server_writes_launch_block_to_mcp_json(settings, tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "notion",
        "description": "Notion workspace",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env": {"NOTION_API_KEY": "abc"},
        "user_confirmed": True,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)

    assert result["success"] is True
    assert result["requires_restart"] is True
    data = json.loads((s.workspace_dir / ".mcp.json").read_text())
    server = data["mcpServers"]["notion"]
    assert server["command"] == "npx"
    assert server["args"] == ["-y", "@notionhq/notion-mcp-server"]
    assert server["env"] == {"NOTION_API_KEY": "abc"}
    assert server["description"] == "Notion workspace"


@pytest.mark.asyncio
async def test_register_mcp_server_refuses_without_user_confirmed(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "notion",
        "description": "Notion workspace",
        "command": "npx",
        "args": ["-y", "x"],
        "user_confirmed": False,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "user_not_confirmed" in result["error"]
    assert not (s.workspace_dir / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_register_mcp_server_rejects_invalid_slug(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "Bad Slug!",
        "description": "x",
        "command": "npx",
        "args": [],
        "user_confirmed": True,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "slug" in result["error"]


@pytest.mark.asyncio
async def test_register_mcp_server_rejects_non_list_args(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "x",
        "description": "x",
        "command": "npx",
        "args": "not-a-list",
        "user_confirmed": True,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "args" in result["error"]


@pytest.mark.asyncio
async def test_register_mcp_server_rejects_non_string_args_elements(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "x",
        "description": "x",
        "command": "npx",
        "args": ["a", 42],
        "user_confirmed": True,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "args" in result["error"]


@pytest.mark.asyncio
async def test_register_mcp_server_rejects_non_dict_env(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "x",
        "description": "x",
        "command": "npx",
        "args": [],
        "env": "not-a-dict",
        "user_confirmed": True,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "env" in result["error"]


@pytest.mark.asyncio
async def test_register_mcp_server_slug_collision_without_replace(tmp_path: Path):
    s = _consent_settings(tmp_path / "ws")
    s.workspace_dir.mkdir()
    # Pre-seed an existing entry
    (s.workspace_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"notion": {"description": "old", "command": "npx", "args": []}}})
    )
    direct_tools.set_capability_index(_make_index([]))

    payload = {
        "slug": "notion",
        "description": "new",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "user_confirmed": True,
        "replace": False,
    }
    result = await direct_tools._execute_register_mcp_server(json.dumps(payload), s)
    assert result["success"] is False
    assert "slug_collision" in result["error"]
