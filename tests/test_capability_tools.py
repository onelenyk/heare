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
async def test_list_capabilities_summary_form_when_count_above_5(settings):
    skills = [_stub_skill_meta(f"sk-{i}") for i in range(12)]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert result["summary"] is True
    assert result["count"] == 12
    assert "12" in result["spoken"]["en"]


@pytest.mark.asyncio
async def test_list_capabilities_full_form_when_count_below_5(settings):
    skills = [_stub_skill_meta(f"sk-{i}") for i in range(3)]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert result["summary"] is False
    assert result["count"] == 3
    assert all(item["name"].startswith("sk-") for item in result["items"])


@pytest.mark.asyncio
async def test_list_capabilities_skips_user_authored(settings):
    skills = [
        _stub_skill_meta("user-skill", installed_via_discovery=False),
        _stub_skill_meta("market-skill", installed_via_discovery=True),
    ]
    fake_loader = MagicMock()
    fake_loader.discover.return_value = skills

    with patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    names = [i["name"] for i in result["items"]]
    assert "market-skill" in names
    assert "user-skill" not in names


@pytest.mark.asyncio
async def test_list_capabilities_empty(settings):
    fake_loader = MagicMock()
    fake_loader.discover.return_value = []
    with patch("src.agent_skills.get_skills_loader", return_value=fake_loader):
        result = await direct_tools._execute_list_capabilities("{}", settings)

    assert result["success"] is True
    assert result["count"] == 0
    assert result["items"] == []


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
