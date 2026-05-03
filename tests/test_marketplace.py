"""Tests for marketplace + MCP registry fetchers (US-005)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src import marketplace
from src.capability_index import IndexEntry


@dataclass
class _FakeSettings:
    marketplace_url: str = "https://skillsmp.com"
    mcp_registry_url: str = "https://skillsmp.com"


def _mock_response(json_data=None, raise_json: bool = False, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if raise_json:
        resp.json = MagicMock(side_effect=json.JSONDecodeError("bad", "", 0))
    else:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _patch_get(response_or_exc):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if isinstance(response_or_exc, Exception):
        client.get = AsyncMock(side_effect=response_or_exc)
    else:
        client.get = AsyncMock(return_value=response_or_exc)
    return patch("src.marketplace.httpx.AsyncClient", return_value=client)


@pytest.mark.asyncio
async def test_fetch_skill_candidates_happy_path():
    payload = {
        "success": True,
        "data": {
            "skills": [
                {
                    "id": "weather-pro-id",
                    "name": "weather-pro",
                    "description": "Look up the weather forecast",
                    "githubUrl": "https://github.com/foo/weather-pro",
                    "stars": 42,
                    "author": "foo",
                }
            ]
        },
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert len(out) == 1
    assert out[0] == IndexEntry(
        source="skill",
        name="weather-pro",
        description="Look up the weather forecast",
        args_schema=None,
        network_required=True,
        popularity_score=42.0,
        install_url="https://github.com/foo/weather-pro",
        checksum=None,
    )


@pytest.mark.asyncio
async def test_bad_hostname_blocked():
    settings = _FakeSettings(marketplace_url="https://evil.com")
    client_factory = MagicMock()
    with patch("src.marketplace.httpx.AsyncClient", client_factory):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_homoglyph_blocked():
    settings = _FakeSettings(marketplace_url="https://g1thub.com")
    client_factory = MagicMock()
    with patch("src.marketplace.httpx.AsyncClient", client_factory):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_subdomain_takeover_blocked():
    settings = _FakeSettings(marketplace_url="https://github.com.evil.com")
    client_factory = MagicMock()
    with patch("src.marketplace.httpx.AsyncClient", client_factory):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_subdomain_match_allowed():
    settings = _FakeSettings(marketplace_url="https://api.skillsmp.com")
    payload = {"results": []}
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []


def test_checksum_verification_match():
    content = b"hello world"
    expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert marketplace._verify_checksum(content, expected) is True


def test_checksum_verification_mismatch():
    content = b"hello world"
    bad = "0" * 64
    assert marketplace._verify_checksum(content, bad) is False


@pytest.mark.asyncio
async def test_malformed_json_returns_empty_logs_warning(caplog):
    settings = _FakeSettings()
    caplog.set_level(logging.WARNING, logger="heare.marketplace")
    with _patch_get(_mock_response(raise_json=True)):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []
    assert any("failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_http_error_returns_empty():
    settings = _FakeSettings()
    with _patch_get(httpx.ConnectError("boom")):
        out = await marketplace.fetch_skill_candidates("weather", settings=settings)
    assert out == []


@pytest.mark.asyncio
async def test_per_result_install_url_validated():
    payload = {
        "success": True,
        "data": {
            "skills": [
                {
                    "name": "good",
                    "description": "ok",
                    "githubUrl": "https://github.com/foo/good",
                },
                {
                    "name": "evil",
                    "description": "bad",
                    "githubUrl": "https://evil.com/foo",
                },
            ]
        },
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_skill_candidates("anything", settings=settings)
    assert len(out) == 1
    assert out[0].name == "good"


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_happy_path():
    payload = {
        "results": [
            {
                "name": "fs",
                "description": "filesystem mcp",
                "install_url": "https://github.com/foo/fs",
            }
        ]
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("files", settings=settings)
    assert len(out) == 1
    assert out[0].source == "mcp"
    assert out[0].name == "fs"
    assert out[0].install_url == "https://github.com/foo/fs"


@pytest.mark.asyncio
async def test_empty_marketplace_url_returns_empty():
    settings = _FakeSettings(marketplace_url="")
    out = await marketplace.fetch_skill_candidates("anything", settings=settings)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_parses_launch_block():
    payload = {
        "results": [
            {
                "name": "fs",
                "description": "filesystem mcp",
                "install_url": "https://github.com/foo/fs",
                "launch": {
                    "command": "npx",
                    "args": ["-y", "@foo/fs-mcp"],
                    "env": {"FS_ROOT": "/tmp"},
                },
            }
        ]
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("files", settings=settings)
    assert len(out) == 1
    assert out[0].launch == {
        "command": "npx",
        "args": ["-y", "@foo/fs-mcp"],
        "env": {"FS_ROOT": "/tmp"},
    }


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_drops_entry_with_missing_launch_command(caplog):
    payload = {
        "results": [
            {
                "name": "broken",
                "description": "no command",
                "install_url": "https://github.com/foo/broken",
                "launch": {"args": ["only", "args"]},
            }
        ]
    }
    settings = _FakeSettings()
    caplog.set_level(logging.WARNING, logger="heare.marketplace")
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("broken", settings=settings)
    assert out == []
    assert any("command must be a non-empty string" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_drops_entry_with_non_list_args(caplog):
    payload = {
        "results": [
            {
                "name": "broken",
                "description": "non-list args",
                "install_url": "https://github.com/foo/broken",
                "launch": {"command": "npx", "args": "not-a-list"},
            }
        ]
    }
    settings = _FakeSettings()
    caplog.set_level(logging.WARNING, logger="heare.marketplace")
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("broken", settings=settings)
    assert out == []
    assert any("args must be a list of strings" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_drops_entry_with_non_string_env_values(caplog):
    payload = {
        "results": [
            {
                "name": "broken",
                "description": "bad env",
                "install_url": "https://github.com/foo/broken",
                "launch": {"command": "npx", "args": [], "env": {"FOO": 123}},
            }
        ]
    }
    settings = _FakeSettings()
    caplog.set_level(logging.WARNING, logger="heare.marketplace")
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("broken", settings=settings)
    assert out == []
    assert any("env must be a dict of str->str" in r.message for r in caplog.records)


def test_coerce_launch_returns_none_when_absent():
    assert marketplace._coerce_launch(None) is None


def test_coerce_launch_normalizes_args_to_list():
    out = marketplace._coerce_launch({"command": "npx", "args": ["a", "b"]})
    assert out == {"command": "npx", "args": ["a", "b"]}


def test_coerce_launch_rejects_non_dict():
    with pytest.raises(ValueError):
        marketplace._coerce_launch("not a dict")


# ----------------------------------------------------------------------
# US-002: built-in MCP catalog
# ----------------------------------------------------------------------


def test_builtin_catalog_has_three_curated_entries():
    slugs = {e.name for e in marketplace._BUILTIN_MCP_CATALOG}
    assert slugs == {"chrome-devtools", "fetch", "memory"}
    for entry in marketplace._BUILTIN_MCP_CATALOG:
        assert entry.source == "mcp"
        assert entry.description
        assert entry.launch is not None
        assert isinstance(entry.launch.get("command"), str) and entry.launch["command"]
        assert isinstance(entry.launch.get("args"), list)


def test_query_builtin_catalog_matches_keyword_browser():
    out = marketplace._query_builtin_catalog("browser")
    names = [e.name for e in out]
    assert "chrome-devtools" in names


def test_query_builtin_catalog_matches_phrase_fetch_url():
    out = marketplace._query_builtin_catalog("fetch url")
    names = [e.name for e in out]
    assert "fetch" in names


def test_query_builtin_catalog_returns_empty_for_irrelevant_query():
    out = marketplace._query_builtin_catalog("salesforce crm")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_returns_catalog_when_registry_empty():
    settings = _FakeSettings(mcp_registry_url="")
    out = await marketplace.fetch_mcp_candidates("browser automation", settings=settings)
    names = [e.name for e in out]
    assert "chrome-devtools" in names


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_network_overrides_catalog_on_slug_collision():
    payload = {
        "results": [
            {
                "name": "chrome-devtools",
                "description": "registry override",
                "install_url": "https://github.com/foo/chrome-devtools",
                "launch": {"command": "node", "args": ["server.js"]},
            }
        ]
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("browser", settings=settings)
    names = [e.name for e in out]
    assert names.count("chrome-devtools") == 1
    overridden = next(e for e in out if e.name == "chrome-devtools")
    assert overridden.description == "registry override"
    assert overridden.launch == {"command": "node", "args": ["server.js"]}


@pytest.mark.asyncio
async def test_fetch_mcp_candidates_merges_network_and_catalog_disjoint_slugs():
    payload = {
        "results": [
            {
                "name": "github",
                "description": "github registry entry",
                "install_url": "https://github.com/foo/github-mcp",
                "launch": {"command": "npx", "args": ["@github/mcp"]},
            }
        ]
    }
    settings = _FakeSettings()
    with _patch_get(_mock_response(payload)):
        out = await marketplace.fetch_mcp_candidates("browser github", settings=settings)
    names = {e.name for e in out}
    assert "github" in names
    assert "chrome-devtools" in names
