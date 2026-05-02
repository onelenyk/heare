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
