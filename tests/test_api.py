"""Tests for src/api.py — minimal HTTP API for daemon control."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import API, tail_lines
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


def _mock_request(
    *, json_data: dict | None = None, query: dict | None = None
) -> MagicMock:
    req = MagicMock()
    if json_data is not None:
        req.json = AsyncMock(return_value=json_data)
    req.query = query if query is not None else {}
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


@pytest.mark.asyncio
async def test_post_provider_known_but_unconfigured(mock_state) -> None:
    """A provider with no key yet can still be selected — token is added later."""
    config = MagicMock()
    config.deepseek_api_key = "sk-test"
    config.zai_api_key = "sk-test"
    config.opencode_api_key = None
    api = API(mock_state, config)
    request = _mock_request(json_data={"provider": "opencode"})
    resp = await api._handle_provider(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    mock_state.set.assert_awaited_once_with("provider", "opencode")


# ---------------------------------------------------------------------------
# POST /model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_model_valid(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": "deepseek", "model": "deepseek-chat"})
    resp = await api._handle_model(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    mock_state.set.assert_awaited_once_with("model_deepseek", "deepseek-chat")


@pytest.mark.asyncio
async def test_post_model_empty(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": "deepseek", "model": ""})
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


@pytest.mark.asyncio
async def test_post_model_unknown_provider(api, mock_state) -> None:
    request = _mock_request(json_data={"provider": "bogus", "model": "x"})
    resp = await api._handle_model(request)
    assert resp.status == 400
    mock_state.set.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/providers, GET /api/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_providers_list(api) -> None:
    request = _mock_request()
    resp = await api._handle_providers_list(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    keys = {p["key"] for p in data}
    assert keys == {"deepseek", "zai", "opencode"}
    assert all(p["configured"] is True for p in data)


@pytest.mark.asyncio
async def test_get_models_live_no_key_falls_back(mock_state) -> None:
    config = MagicMock()
    config.deepseek_api_key = None
    config.zai_api_key = None
    config.opencode_api_key = None
    api = API(mock_state, config)
    request = MagicMock()
    request.query = {"provider": "deepseek"}
    resp = await api._handle_models_live(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["source"] == "fallback"
    assert "deepseek-chat" in data["models"]


@pytest.mark.asyncio
async def test_get_models_live_unknown_provider(api) -> None:
    request = MagicMock()
    request.query = {"provider": "bogus"}
    resp = await api._handle_models_live(request)
    assert resp.status == 400


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
# POST /api/settings/passphrase, POST /api/settings/reset-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_passphrase_valid(api, monkeypatch) -> None:
    called = {}
    monkeypatch.setattr(
        "src.api.set_confirmation_passphrase", lambda word: called.setdefault("word", word)
    )
    request = _mock_request(json_data={"passphrase": "авторизую"})
    resp = await api._handle_set_passphrase(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["restart_required"] is True
    assert called["word"] == "авторизую"


@pytest.mark.asyncio
async def test_set_passphrase_empty(api, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.api.set_confirmation_passphrase",
        lambda word: pytest.fail("should not be called"),
    )
    request = _mock_request(json_data={"passphrase": "  "})
    resp = await api._handle_set_passphrase(request)
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_reset_session_found(api, monkeypatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(
        "src.api.backup_session_file", lambda settings: Path("/tmp/session_0.backup.json")
    )
    request = _mock_request()
    resp = await api._handle_reset_session(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["backup_path"] == "/tmp/session_0.backup.json"
    assert data["restart_required"] is True


@pytest.mark.asyncio
async def test_reset_session_none(api, monkeypatch) -> None:
    monkeypatch.setattr("src.api.backup_session_file", lambda settings: None)
    request = _mock_request()
    resp = await api._handle_reset_session(request)
    assert resp.status == 400
    data = json.loads(resp.body)
    assert data["ok"] is False


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
    assert ("GET", "/api/providers") in pairs
    assert ("GET", "/api/models") in pairs
    assert ("POST", "/api/settings/passphrase") in pairs
    assert ("POST", "/api/settings/reset-session") in pairs
    assert ("POST", "/cancel") in pairs


def test_mode_values_are_valid() -> None:
    assert all(isinstance(m, str) and m for m in VALID_MODES)


# ---------------------------------------------------------------------------
# tail_lines — the daemon log is read on every dashboard poll, so it must not
# scale with file size. These pin the seek-from-the-end behaviour.
# ---------------------------------------------------------------------------


def test_tail_lines_file_smaller_than_window(tmp_path) -> None:
    f = tmp_path / "daemon.log"
    f.write_text("a\nb\nc\n")
    assert tail_lines(f, 2) == ["b", "c"]


def test_tail_lines_count_exceeds_file(tmp_path) -> None:
    """Asking for more lines than exist returns everything, not an error."""
    f = tmp_path / "daemon.log"
    f.write_text("only\ntwo\n")
    assert tail_lines(f, 500) == ["only", "two"]


def test_tail_lines_exact_count(tmp_path) -> None:
    f = tmp_path / "daemon.log"
    f.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")
    assert tail_lines(f, 10) == [f"line{i}" for i in range(10)]


def test_tail_lines_empty_file(tmp_path) -> None:
    f = tmp_path / "daemon.log"
    f.write_text("")
    assert tail_lines(f, 10) == []


def test_tail_lines_grows_window_for_long_lines(tmp_path) -> None:
    """A tail longer than the initial window must trigger a wider re-read.

    The real log carries multi-KB single lines (system-prompt dumps), so a
    fixed window can come back short. Uses a small window rather than a
    multi-megabyte fixture.
    """
    f = tmp_path / "daemon.log"
    f.write_text("\n".join("x" * 200 for _ in range(50)) + "\n")
    got = tail_lines(f, 20, window=64)
    assert len(got) == 20
    assert all(line == "x" * 200 for line in got)


def test_tail_lines_drops_partial_first_line(tmp_path) -> None:
    """A window landing mid-line must not emit that truncated fragment."""
    f = tmp_path / "daemon.log"
    f.write_text("HEADER-THAT-GETS-CUT\nsecond\nthird\n")
    got = tail_lines(f, 10, window=16, max_bytes=16)
    assert "HEADER-THAT-GETS-CUT" not in got
    assert got == [ln for ln in got if not ln.startswith("HEADER")]


def test_tail_lines_respects_max_bytes(tmp_path) -> None:
    """Growth stops at max_bytes even if that yields fewer lines than asked."""
    f = tmp_path / "daemon.log"
    f.write_text("\n".join(f"line{i}" for i in range(1000)) + "\n")
    got = tail_lines(f, 900, window=64, max_bytes=128)
    assert len(got) < 900
    assert got[-1] == "line999"


# ---------------------------------------------------------------------------
# GET /logs
# ---------------------------------------------------------------------------


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "daemon.log").write_text("\n".join(f"log{i}" for i in range(400)) + "\n")
    return d


@pytest.mark.asyncio
async def test_get_logs_default_limit(api, mock_config, log_dir) -> None:
    mock_config.log_dir = log_dir
    resp = await api._handle_logs(_mock_request())
    lines = json.loads(resp.body)["lines"]
    assert len(lines) == 200
    assert lines[-1] == "log399"


@pytest.mark.asyncio
async def test_get_logs_respects_limit(api, mock_config, log_dir) -> None:
    mock_config.log_dir = log_dir
    resp = await api._handle_logs(_mock_request(query={"limit": "5"}))
    assert json.loads(resp.body)["lines"] == [f"log{i}" for i in range(395, 400)]


@pytest.mark.asyncio
async def test_get_logs_clamps_limit(api, mock_config, log_dir) -> None:
    mock_config.log_dir = log_dir
    high = await api._handle_logs(_mock_request(query={"limit": "99999"}))
    assert len(json.loads(high.body)["lines"]) == 400  # capped at 1000, file has 400
    low = await api._handle_logs(_mock_request(query={"limit": "0"}))
    assert len(json.loads(low.body)["lines"]) == 1
    junk = await api._handle_logs(_mock_request(query={"limit": "abc"}))
    assert len(json.loads(junk.body)["lines"]) == 200  # falls back to default


@pytest.mark.asyncio
async def test_get_logs_missing_file(api, mock_config, tmp_path) -> None:
    mock_config.log_dir = tmp_path / "nope"
    resp = await api._handle_logs(_mock_request())
    assert json.loads(resp.body)["lines"] == []


# ---------------------------------------------------------------------------
# GET /activity — real SQLite so the paging SQL itself is covered
# ---------------------------------------------------------------------------


@pytest.fixture
def activity_db(tmp_path):
    db_path = tmp_path / "heare.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE transcripts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, text TEXT NOT NULL, "
        "mode TEXT NOT NULL, agent_spoken INTEGER)"
    )
    for i in range(1, 121):
        conn.execute(
            "INSERT INTO transcripts (ts, text, mode, agent_spoken) VALUES (?, ?, ?, ?)",
            (1000.0 + i, f"msg{i}", "assistant" if i % 2 else "user", 1),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_activity_default_limit(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    resp = await api._handle_activity(_mock_request())
    rows = json.loads(resp.body)
    assert len(rows) == 50
    assert rows[0]["content"] == "msg120"  # newest first


@pytest.mark.asyncio
async def test_activity_includes_id_for_paging(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    resp = await api._handle_activity(_mock_request(query={"limit": "3"}))
    rows = json.loads(resp.body)
    assert [r["id"] for r in rows] == [120, 119, 118]


@pytest.mark.asyncio
async def test_activity_before_id_pages_backwards(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    first = json.loads(
        (await api._handle_activity(_mock_request(query={"limit": "10"}))).body
    )
    older = json.loads(
        (
            await api._handle_activity(
                _mock_request(query={"limit": "10", "before_id": str(first[-1]["id"])})
            )
        ).body
    )
    assert [r["id"] for r in older] == list(range(110, 100, -1))
    assert not {r["id"] for r in first} & {r["id"] for r in older}  # no overlap


@pytest.mark.asyncio
async def test_activity_clamps_limit(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    resp = await api._handle_activity(_mock_request(query={"limit": "99999"}))
    assert len(json.loads(resp.body)) == 120  # capped at 500, table has 120


@pytest.mark.asyncio
async def test_activity_maps_speaker(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    resp = await api._handle_activity(_mock_request(query={"limit": "2"}))
    rows = json.loads(resp.body)
    # 'assistant' maps to bot; every other mode collapses to 'you'
    assert rows[0]["who"] == "you" and rows[0]["content"] == "msg120"
    assert rows[1]["who"] == "bot" and rows[1]["content"] == "msg119"
