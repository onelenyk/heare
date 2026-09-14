"""Tests for the API's intents endpoints — GET /api/intents and POST /api/intents/{id}/drop."""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import API
from src.spine import intents as I


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.snapshot.return_value = {"mode": "focus", "provider": "deepseek"}
    state.get_bool.return_value = False
    state.get.return_value = ""
    state.set = AsyncMock()
    state.set_bool = AsyncMock()
    return state


@pytest.fixture
def mock_config(tmp_path):
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.db_path = tmp_path / "state.db"
    return config


@pytest.fixture
def api(mock_state, mock_config):
    return API(mock_state, mock_config)


def _mock_request(
    *, json_data: dict | None = None, query: dict | None = None, match_info: dict | None = None
) -> MagicMock:
    req = MagicMock()
    if json_data is not None:
        req.json = AsyncMock(return_value=json_data)
    req.query = query if query is not None else {}
    req.match_info = match_info if match_info is not None else {}
    return req


# ── GET /api/intents ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intents_no_store(api):
    """When engine is not running, return empty list with note."""
    api._intents = None
    request = _mock_request()
    response = await api._handle_intents(request)
    data = json.loads(response.text)
    assert data["ok"] is True
    assert data["intents"] == []
    assert "engine not running" in data.get("note", "")


@pytest.mark.asyncio
async def test_intents_with_pending(api):
    """Return pending intents with all required fields."""
    mock_store = MagicMock()
    intent1 = I.Intent(
        id=1,
        kind="job_done",
        text="task finished",
        origin=I.SELF,
        urgency=0.8,
        state=I.PENDING,
        created_ts=time.time() - 100,
        updated_ts=time.time() - 100,
        due_ts=None,
        voiced_ts=None,
        outcome=None,
        dedupe_key=None,
        expires_ts=None,
    )
    intent2 = I.Intent(
        id=2,
        kind="reminder",
        text="check email",
        origin=I.USER,
        urgency=0.5,
        state=I.PENDING,
        created_ts=time.time() - 50,
        updated_ts=time.time() - 50,
        due_ts=time.time() + 3600,
        voiced_ts=None,
        outcome=None,
        dedupe_key=None,
        expires_ts=None,
    )
    mock_store.pending = AsyncMock(return_value=[intent1, intent2])
    mock_store.awaiting_reaction = AsyncMock(return_value=None)
    api._intents = mock_store

    request = _mock_request()
    response = await api._handle_intents(request)
    data = json.loads(response.text)

    assert data["ok"] is True
    assert len(data["intents"]) == 2
    assert data["intents"][0]["id"] == 1
    assert data["intents"][0]["kind"] == "job_done"
    assert data["intents"][0]["text"] == "task finished"
    assert data["intents"][0]["origin"] == I.SELF
    assert data["intents"][0]["state"] == I.PENDING
    assert data["intents"][0]["urgency"] == 0.8
    assert data["intents"][0]["created_ts"] is not None
    assert data["intents"][1]["id"] == 2


@pytest.mark.asyncio
async def test_intents_with_awaiting_reaction(api):
    """Include voiced intent awaiting reaction in the list."""
    mock_store = MagicMock()
    mock_store.pending = AsyncMock(return_value=[])
    awaiting = I.Intent(
        id=99,
        kind="reminder",
        text="called you",
        origin=I.SELF,
        urgency=0.7,
        state=I.VOICED,
        created_ts=time.time() - 200,
        updated_ts=time.time() - 200,
        due_ts=None,
        voiced_ts=time.time() - 100,
        outcome=None,
        dedupe_key=None,
        expires_ts=None,
    )
    mock_store.awaiting_reaction = AsyncMock(return_value=awaiting)
    api._intents = mock_store

    request = _mock_request()
    response = await api._handle_intents(request)
    data = json.loads(response.text)

    assert data["ok"] is True
    assert len(data["intents"]) == 1
    assert data["intents"][0]["id"] == 99
    assert data["intents"][0]["state"] == I.VOICED


@pytest.mark.asyncio
async def test_intents_deduplicates_voiced(api):
    """Don't duplicate a voiced intent if it's already in pending."""
    mock_store = MagicMock()
    intent = I.Intent(
        id=1,
        kind="reminder",
        text="same intent",
        origin=I.SELF,
        urgency=0.6,
        state=I.PENDING,
        created_ts=time.time() - 100,
        updated_ts=time.time() - 100,
        due_ts=None,
        voiced_ts=None,
        outcome=None,
        dedupe_key=None,
        expires_ts=None,
    )
    mock_store.pending = AsyncMock(return_value=[intent])
    mock_store.awaiting_reaction = AsyncMock(return_value=intent)  # Same ID
    api._intents = mock_store

    request = _mock_request()
    response = await api._handle_intents(request)
    data = json.loads(response.text)

    assert data["ok"] is True
    assert len(data["intents"]) == 1  # Not duplicated


# ── POST /api/intents/{id}/drop ──────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_drop_no_store(api):
    """Return 503 when engine is not running."""
    api._intents = None
    request = _mock_request(match_info={"id": "1"})
    response = await api._handle_intent_drop(request)
    assert response.status == 503
    data = json.loads(response.text)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_intent_drop_invalid_id(api):
    """Return 400 when id is not a valid integer."""
    mock_store = MagicMock()
    api._intents = mock_store
    request = _mock_request(match_info={"id": "not-an-int"})
    response = await api._handle_intent_drop(request)
    assert response.status == 400
    data = json.loads(response.text)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_intent_drop_success(api):
    """Successfully drop an intent by id."""
    mock_store = MagicMock()
    mock_store.drop = AsyncMock()
    api._intents = mock_store
    request = _mock_request(match_info={"id": "42"})
    response = await api._handle_intent_drop(request)
    assert response.status == 200
    data = json.loads(response.text)
    assert data["ok"] is True
    assert data["id"] == 42
    mock_store.drop.assert_called_once_with(42, reason="dashboard")


# ── GET /state with judge field ──────────────────────────────────────

@pytest.mark.asyncio
async def test_state_includes_judge_when_present(api, mock_state):
    """The /state endpoint includes the judge field when engine_verdict is set."""
    mock_state.get = MagicMock(side_effect=lambda key, default="": {
        "engine_verdict": json.dumps({
            "speak": False,
            "reason": "too soon",
            "intent_id": None,
            "intent_kind": None,
            "intent_text": None,
            "trust": 1.5,
            "ts": time.time(),
        }),
    }.get(key, default))
    request = _mock_request()
    response = await api._handle_state(request)
    data = json.loads(response.text)
    assert data["judge"] is not None
    assert data["judge"]["speak"] is False
    assert data["judge"]["reason"] == "too soon"
    assert data["judge"]["trust"] == 1.5


@pytest.mark.asyncio
async def test_state_judge_none_when_absent(api, mock_state):
    """The judge field is None when engine_verdict is not set."""
    mock_state.get = MagicMock(return_value="")
    request = _mock_request()
    response = await api._handle_state(request)
    data = json.loads(response.text)
    assert data.get("judge") is None
