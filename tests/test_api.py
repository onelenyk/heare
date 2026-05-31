"""Tests for src/api.py — minimal HTTP API for daemon control."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import API
from src.agent.modes import VALID_MODES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.snapshot.return_value = {"mode": "focus", "provider": "deepseek"}
    state.get_bool.return_value = False
    state.set = AsyncMock()
    state.set_bool = AsyncMock()
    return state


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = "sk-test"
    config.opencode_api_key = "sk-test"
    return config


@pytest.fixture
def api(mock_state, mock_config):
    return API(mock_state, mock_config)


# ── Helpers ───────────────────────────────────────────────────────────


def _mock_request(*, json_data: dict | None = None) -> MagicMock:
    req = MagicMock()
    if json_data is not None:
        req.json = AsyncMock(return_value=json_data)
    return req


# ---------------------------------------------------------------------------
# _available_providers
# ---------------------------------------------------------------------------


def test_available_providers_none(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = None
    config.zai_api_key = None
    config.opencode_api_key = None
    api = API(mock_state, config)
    assert api._available_providers() == []


def test_available_providers_deepseek_only(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = None
    config.opencode_api_key = None
    api = API(mock_state, config)
    assert api._available_providers() == ["deepseek"]


def test_available_providers_zai_only(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = None
    config.zai_api_key = "sk-test"
    config.opencode_api_key = None
    api = API(mock_state, config)
    assert api._available_providers() == ["zai"]


def test_available_providers_opencode_only(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = None
    config.zai_api_key = None
    config.opencode_api_key = "sk-test"
    api = API(mock_state, config)
    assert api._available_providers() == ["opencode"]


def test_available_providers_all(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = "sk-test"
    config.opencode_api_key = "sk-test"
    api = API(mock_state, config)
    providers = api._available_providers()
    assert "deepseek" in providers
    assert "zai" in providers
    assert "opencode" in providers
    assert len(providers) == 3


# ---------------------------------------------------------------------------
# GET /state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_state(api, mock_state) -> None:
    request = _mock_request()
    resp = await api._handle_state(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["mode"] == "focus"
    assert data["provider"] == "deepseek"
    assert "providers" in data
    assert isinstance(data["providers"], list)


@pytest.mark.asyncio
async def test_get_state_providers_reflect_config(
    mock_state, mock_config
) -> None:
    mock_config.deepseek_api_key = "sk-test"
    mock_config.zai_api_key = None
    mock_config.opencode_api_key = None
    api = API(mock_state, mock_config)
    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)
    assert data["providers"] == ["deepseek"]


# ---------------------------------------------------------------------------
# POST /mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mode_valid(api, mock_state) -> None:
    request = _mock_request(json_data={"mode": "silent"})
    resp = await api._handle_mode(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["mode"] == "silent"
    mock_state.set.assert_awaited_once_with("mode", "silent")


@pytest.mark.asyncio
async def test_post_mode_defaults_to_focus(api, mock_state) -> None:
    request = _mock_request(json_data={})
    resp = await api._handle_mode(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["mode"] == "focus"
    mock_state.set.assert_awaited_once_with("mode", "focus")


@pytest.mark.asyncio
async def test_post_mode_invalid(api, mock_state) -> None:
    request = _mock_request(json_data={"mode": "bogus"})
    resp = await api._handle_mode(request)
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["ok"] is False
    mock_state.set.assert_not_called()


@pytest.mark.asyncio
async def test_post_mode_rejects_empty_string(api, mock_state) -> None:
    request = _mock_request(json_data={"mode": ""})
    resp = await api._handle_mode(request)
    assert resp.status == 400
    mock_state.set.assert_not_called()


# ---------------------------------------------------------------------------
# POST /mute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_mute_speaker_default(api, mock_state) -> None:
    mock_state.get_bool.return_value = False
    request = _mock_request(json_data={})
    resp = await api._handle_mute(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["target"] == "speaker"
    assert data["muted"] is True
    mock_state.get_bool.assert_called_once_with("mute_bot")
    mock_state.set_bool.assert_awaited_once_with("mute_bot", True)


@pytest.mark.asyncio
async def test_post_mute_speaker_toggle(api, mock_state) -> None:
    mock_state.get_bool.return_value = True
    request = _mock_request(json_data={"target": "speaker"})
    resp = await api._handle_mute(request)
    data = json.loads(resp.body)
    assert data["muted"] is False
    mock_state.set_bool.assert_awaited_once_with("mute_bot", False)


@pytest.mark.asyncio
async def test_post_mute_mic(api, mock_state) -> None:
    mock_state.get_bool.return_value = False
    request = _mock_request(json_data={"target": "mic"})
    resp = await api._handle_mute(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["target"] == "mic"
    assert data["muted"] is True
    mock_state.get_bool.assert_called_once_with("mute_mic")
    mock_state.set_bool.assert_awaited_once_with("mute_mic", True)


# ---------------------------------------------------------------------------
# POST /provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_provider_valid(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": "zai"})
    resp = await api._handle_provider(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["provider"] == "zai"
    mock_state.set.assert_awaited_once_with("provider", "zai")


@pytest.mark.asyncio
async def test_post_provider_unavailable(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": "bogus"})
    resp = await api._handle_provider(request)
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["ok"] is False
    mock_state.set.assert_not_called()


@pytest.mark.asyncio
async def test_post_provider_empty(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": ""})
    resp = await api._handle_provider(request)
    assert resp.status == 400
    mock_state.set.assert_not_called()


# ---------------------------------------------------------------------------
# POST /model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_model_valid(api, mock_state) -> None:
    request = _mock_request(json_data={"model": "deepseek-chat"})
    resp = await api._handle_model(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["model"] == "deepseek-chat"
    mock_state.set.assert_awaited_once_with("model", "deepseek-chat")


@pytest.mark.asyncio
async def test_post_model_empty(api, mock_state) -> None:
    request = _mock_request(json_data={"model": ""})
    resp = await api._handle_model(request)
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["ok"] is False
    mock_state.set.assert_not_called()


@pytest.mark.asyncio
async def test_post_model_missing_key(api, mock_state) -> None:
    request = _mock_request(json_data={})
    resp = await api._handle_model(request)
    assert resp.status == 400
    mock_state.set.assert_not_called()


# ---------------------------------------------------------------------------
# POST /cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_cancel(api, mock_state) -> None:
    request = _mock_request()
    resp = await api._handle_cancel(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    mock_state.set.assert_awaited_once_with("cancel", "1")


# ---------------------------------------------------------------------------
# Integration: multiple routes registered
# ---------------------------------------------------------------------------


def test_routes_registered(api) -> None:
    pairs = []
    for r in api._app.router.resources():
        for route in r:
            pairs.append((route.method, r.canonical))
    assert ("GET", "/state") in pairs
    assert ("POST", "/mode") in pairs
    assert ("POST", "/mute") in pairs
    assert ("POST", "/provider") in pairs
    assert ("POST", "/model") in pairs
    assert ("POST", "/cancel") in pairs


def test_mode_values_are_valid() -> None:
    assert all(isinstance(m, str) and m for m in VALID_MODES)
