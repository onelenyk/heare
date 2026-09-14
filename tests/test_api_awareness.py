"""Tests for /state endpoint awareness fields: ears and job."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import API


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.snapshot.return_value = {"mode": "focus", "provider": "deepseek"}
    state.get_bool.return_value = False
    state.set = AsyncMock()
    state.set_bool = AsyncMock()
    return state


@pytest.fixture
def mock_config(tmp_path):
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = "sk-test"
    config.opencode_api_key = "sk-test"
    config.openrouter_api_key = "sk-test"
    config.db_path = tmp_path / "state.db"
    return config


@pytest.fixture
def api(mock_state, mock_config):
    return API(mock_state, mock_config)


def _mock_request(*, json_data: dict | None = None, query: dict | None = None):
    req = MagicMock()
    if json_data is not None:
        req.json = AsyncMock(return_value=json_data)
    req.query = query if query is not None else {}
    return req


@pytest.mark.asyncio
async def test_ears_parsed_from_state(api, mock_state) -> None:
    """ears is parsed from the JSON snapshot; None when absent."""
    ears_json = json.dumps({"awake": True, "gate": True, "duplex": True})
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
        "ears": ears_json,
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["ears"] is not None
    assert data["ears"]["awake"] is True
    assert data["ears"]["gate"] is True
    assert data["ears"]["duplex"] is True


@pytest.mark.asyncio
async def test_ears_none_when_absent(api, mock_state) -> None:
    """ears is None when not in the state snapshot."""
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["ears"] is None


@pytest.mark.asyncio
async def test_ears_none_on_malformed_json(api, mock_state) -> None:
    """ears is None when the JSON is malformed."""
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
        "ears": "not valid json",
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["ears"] is None


@pytest.mark.asyncio
async def test_job_parsed_from_state(api, mock_state) -> None:
    """job is parsed from the JSON snapshot; None when absent."""
    job_json = json.dumps({
        "id": 1,
        "label": "test",
        "task": "do something",
        "started_ts": 1234567890.0,
    })
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
        "job": job_json,
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["job"] is not None
    assert data["job"]["id"] == 1
    assert data["job"]["label"] == "test"
    assert data["job"]["task"] == "do something"
    assert data["job"]["started_ts"] == 1234567890.0


@pytest.mark.asyncio
async def test_job_none_when_absent(api, mock_state) -> None:
    """job is None when not in the state snapshot."""
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["job"] is None


@pytest.mark.asyncio
async def test_job_none_when_empty_string(api, mock_state) -> None:
    """job is None when the state value is an empty string."""
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
        "job": "",
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["job"] is None


@pytest.mark.asyncio
async def test_job_none_on_malformed_json(api, mock_state) -> None:
    """job is None when the JSON is malformed."""
    mock_state.snapshot.return_value = {
        "mode": "focus",
        "provider": "deepseek",
        "job": "not valid json",
    }

    request = _mock_request()
    resp = await api._handle_state(request)
    data = json.loads(resp.body)

    assert data["job"] is None
