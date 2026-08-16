"""Tests for src/api.py — minimal HTTP API for daemon control."""
from __future__ import annotations

import json
import os
import sqlite3
import time
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
# POST /api/settings/allow-installs, POST /api/settings/reset-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allow_installs_valid(api, monkeypatch) -> None:
    called = {}
    monkeypatch.setattr(
        "src.api.set_capability_install_enabled",
        lambda enabled: called.setdefault("enabled", enabled),
    )
    request = _mock_request(json_data={"enabled": True})
    resp = await api._handle_allow_installs(request)
    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert data["enabled"] is True
    assert data["restart_required"] is True
    assert called["enabled"] is True


@pytest.mark.asyncio
async def test_allow_installs_off_is_a_valid_choice(api, monkeypatch) -> None:
    """Turning it off must reach the setter, not be read as a missing value."""
    called = {}
    monkeypatch.setattr(
        "src.api.set_capability_install_enabled",
        lambda enabled: called.setdefault("enabled", enabled),
    )
    resp = await api._handle_allow_installs(_mock_request(json_data={"enabled": False}))
    assert resp.status == 200
    assert called["enabled"] is False


@pytest.mark.asyncio
async def test_allow_installs_rejects_a_non_boolean(api, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.api.set_capability_install_enabled",
        lambda enabled: pytest.fail("should not be called"),
    )
    resp = await api._handle_allow_installs(_mock_request(json_data={"enabled": "yes"}))
    assert resp.status == 400
    assert json.loads(resp.body)["ok"] is False


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
    assert ("POST", "/api/settings/allow-installs") in pairs
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
        "mode TEXT NOT NULL, agent_spoken INTEGER, source TEXT)"
    )
    # The activity feed UNIONs actions ('did' rows) with transcripts, so the
    # fixture must carry both tables even when a test only inspects transcripts.
    conn.execute(
        "CREATE TABLE actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, status TEXT, "
        "result_summary TEXT, tool TEXT, args TEXT)"
    )
    for i in range(1, 121):
        conn.execute(
            "INSERT INTO transcripts (ts, text, mode, agent_spoken, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                1000.0 + i,
                f"msg{i}",
                "assistant" if i % 2 else "user",
                # Mirrors the real table: agent rows carry the flag, user
                # rows never did (NULL in every legacy row on disk).
                1 if i % 2 else None,
                # Leave most rows NULL so the "pre-migration reads as voice"
                # path is the one under test; one explicit typed row.
                "typed" if i == 120 else None,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def activity_db_with_action(activity_db):
    """activity_db plus one 'did' row, timestamped between msg120 and msg119."""
    conn = sqlite3.connect(activity_db)
    conn.execute(
        "INSERT INTO actions (ts, status, tool, args) VALUES (?, ?, ?, ?)",
        (1120.5, "done", "bash", "df -h /"),
    )
    conn.commit()
    conn.close()
    return activity_db


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
    # Composite ids namespace the two source tables: 't' for transcripts.
    assert [r["id"] for r in rows] == ["t120", "t119", "t118"]


@pytest.mark.asyncio
async def test_activity_before_ts_pages_backwards(api, mock_config, activity_db) -> None:
    mock_config.db_path = activity_db
    first = json.loads(
        (await api._handle_activity(_mock_request(query={"limit": "10"}))).body
    )
    older = json.loads(
        (
            await api._handle_activity(
                _mock_request(
                    query={"limit": "10", "before_ts": str(first[-1]["ts"])}
                )
            )
        ).body
    )
    assert [r["id"] for r in older] == [f"t{i}" for i in range(110, 100, -1)]
    assert not {r["id"] for r in first} & {r["id"] for r in older}  # no overlap


@pytest.mark.asyncio
async def test_activity_includes_agent_actions(
    api, mock_config, activity_db_with_action
) -> None:
    """The agent's tool calls surface as 'did' rows, interleaved by ts."""
    mock_config.db_path = activity_db_with_action
    resp = await api._handle_activity(_mock_request(query={"limit": "3"}))
    rows = json.loads(resp.body)
    # ts order: msg120 (1120) < action (1120.5) < msg119 (1119)? No — newest
    # first: msg120=1120, action=1120.5 is newest, msg119=1119.
    did = [r for r in rows if r["type"] == "did"]
    assert len(did) == 1
    assert did[0]["id"] == "a1" and did[0]["who"] == "agent"
    assert did[0]["tool"] == "bash" and "df -h /" in did[0]["content"]
    assert did[0]["status"] == "done"


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


@pytest.mark.asyncio
async def test_activity_reports_source(api, mock_config, activity_db) -> None:
    """Provenance rides along so the panel can mark typed turns."""
    mock_config.db_path = activity_db
    resp = await api._handle_activity(_mock_request(query={"limit": "2"}))
    rows = json.loads(resp.body)
    assert rows[0]["source"] == "typed"
    # NULL predates the column — those turns did arrive by mic.
    assert rows[1]["source"] == "voice"


# ---------------------------------------------------------------------------
# POST /inject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inject_writes_atomically(api, mock_config, mock_state, tmp_path) -> None:
    """The daemon polls this folder every 250ms; a half-written file would be
    read as a truncated message, so publish must be a rename."""
    mock_config.inject_dir = tmp_path / "inject"
    mock_state.get.return_value = "true"
    resp = await api._handle_inject(_mock_request(json_data={"text": "hello"}))
    assert json.loads(resp.body)["ok"] is True

    files = list((tmp_path / "inject").iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".txt"          # no .tmp left behind
    assert files[0].read_text() == "hello"


@pytest.mark.asyncio
async def test_inject_reports_delivery_vs_queueing(api, mock_config, mock_state, tmp_path) -> None:
    """Queued and delivered differ — nothing drains the folder while stopped."""
    mock_config.inject_dir = tmp_path / "inject"

    mock_state.get.return_value = "true"
    running = await api._handle_inject(_mock_request(json_data={"text": "a"}))
    assert json.loads(running.body)["delivered"] is True

    mock_state.get.return_value = "false"
    stopped = await api._handle_inject(_mock_request(json_data={"text": "b"}))
    body = json.loads(stopped.body)
    assert body["ok"] is True and body["delivered"] is False
    # Still queued for whenever the pipeline comes back.
    assert len(list((tmp_path / "inject").iterdir())) == 2


@pytest.mark.asyncio
async def test_inject_rejects_empty(api, mock_config, tmp_path) -> None:
    mock_config.inject_dir = tmp_path / "inject"
    resp = await api._handle_inject(_mock_request(json_data={"text": "   "}))
    assert resp.status == 400
    assert json.loads(resp.body)["ok"] is False


@pytest.mark.asyncio
async def test_state_model_reflects_override(mock_state, mock_config) -> None:
    """The active model shown must be the per-provider override, not the stale
    top-level 'model' snapshot that goes unset after a hot-swap."""
    mock_state.snapshot.return_value = {
        "provider": "deepseek",
        "model": "deepseek-chat",          # stale startup snapshot
        "model_deepseek": "deepseek-v4-flash",  # live override via /model
    }
    api = API(mock_state, mock_config)
    resp = await api._handle_state(_mock_request())
    data = json.loads(resp.body)
    assert data["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_state_model_falls_back_to_default(mock_state, mock_config) -> None:
    """With no override the active model is the provider default."""
    mock_state.snapshot.return_value = {"provider": "deepseek", "model": "stale"}
    api = API(mock_state, mock_config)
    resp = await api._handle_state(_mock_request())
    data = json.loads(resp.body)
    from src.agent.llm.providers import get_config

    assert data["model"] == get_config("deepseek").default_model


# ---------------------------------------------------------------------------
# API keys — one path for every UI surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_never_ships_raw_api_keys(mock_state, mock_config) -> None:
    """GET /state is unauthenticated — it must expose "is it set?", never the
    secret. The raw value stays in state because that is how a hot-swapped key
    reaches the LLM service."""
    mock_state.snapshot.return_value = {
        "provider": "deepseek",
        "key_deepseek_api_key": "sk-notarealkey0000000000000000000000",
        "key_groq_api_key": "gsk_realsecret",
    }
    api = API(mock_state, mock_config)
    resp = await api._handle_state(_mock_request())
    body = resp.body.decode()

    assert "sk-notarealkey0000000000000000000000" not in body
    assert "gsk_realsecret" not in body
    data = json.loads(body)
    assert data["key_deepseek_api_key"] is True
    assert data["key_groq_api_key"] is True


@pytest.mark.asyncio
async def test_state_reports_unset_key_as_false(mock_state, mock_config) -> None:
    mock_state.snapshot.return_value = {"key_zai_api_key": ""}
    api = API(mock_state, mock_config)
    resp = await api._handle_state(_mock_request())
    assert json.loads(resp.body)["key_zai_api_key"] is False


def test_normalise_key_payload_accepts_both_spellings(api) -> None:
    """The dashboard sends attribute names, the setup modal sends env names."""
    keys = api._normalise_key_payload(
        {
            "deepseek_api_key": "sk-" + "d" * 30,
            "ZAI_API_KEY": "sk-" + "z" * 30,
            "language": "uk",
            "opencode_api_key": "   ",
            "NOT_A_KEY": "whatever",
        }
    )
    assert keys == {
        "deepseek_api_key": "sk-" + "d" * 30,
        "zai_api_key": "sk-" + "z" * 30,
    }


def test_validate_api_keys(api) -> None:
    assert api._validate_api_keys({"groq_api_key": "gsk_ok"}) == []
    assert api._validate_api_keys({"groq_api_key": "nope"}) == [
        "Groq key must start with gsk_ or sk-"
    ]
    assert api._validate_api_keys({"deepseek_api_key": "short"}) == [
        "Deepseek Api Key key looks too short"
    ]


@pytest.mark.asyncio
async def test_apply_api_keys_persists_env_config_and_state(
    mock_state, mock_config, tmp_path, monkeypatch
) -> None:
    """One paste must land in all three places: the .env file (durable), the
    Settings object (this process) and state (hot-swap into the LLM)."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    api = API(mock_state, mock_config)

    key = "sk-" + "a" * 30
    await api._apply_api_keys({"deepseek_api_key": key})

    assert f"DEEPSEEK_API_KEY={key}" in (tmp_path / ".env").read_text()
    assert os.environ["DEEPSEEK_API_KEY"] == key
    assert mock_config.deepseek_api_key == key
    mock_state.set.assert_any_await("key_deepseek_api_key", key)


@pytest.mark.asyncio
async def test_setup_config_and_settings_share_the_key_path(
    mock_state, mock_config, tmp_path, monkeypatch
) -> None:
    """Both endpoints must apply a key live — the setup modal used to write
    only the .env file, so the running daemon kept the old key."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    api = API(mock_state, mock_config)

    key = "sk-" + "b" * 30
    resp = await api._handle_setup_config(
        _mock_request(json_data={"DEEPSEEK_API_KEY": key})
    )
    assert json.loads(resp.body) == {"ok": True, "applied": True}
    mock_state.set.assert_any_await("key_deepseek_api_key", key)


@pytest.mark.asyncio
async def test_setup_config_rejects_bad_key(
    mock_state, mock_config, tmp_path, monkeypatch
) -> None:
    """It used to accept anything; now it validates like /settings does."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    api = API(mock_state, mock_config)
    resp = await api._handle_setup_config(
        _mock_request(json_data={"GROQ_API_KEY": "bogus"})
    )
    assert resp.status == 400
    assert not (tmp_path / ".env").exists()


# ---------------------------------------------------------------------------
# GET /api/artifacts, GET /api/artifacts/{name}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifacts_list_missing_dir(api, mock_config, tmp_path) -> None:
    mock_config.workspace_dir = tmp_path / "nope"
    resp = await api._handle_artifacts_list(_mock_request())
    assert resp.status == 200
    assert json.loads(resp.body) == {"ok": True, "artifacts": []}


@pytest.mark.asyncio
async def test_artifacts_list_empty_dir(api, mock_config, tmp_path) -> None:
    mock_config.workspace_dir = tmp_path
    (tmp_path / "artifacts").mkdir()
    resp = await api._handle_artifacts_list(_mock_request())
    assert json.loads(resp.body) == {"ok": True, "artifacts": []}


@pytest.mark.asyncio
async def test_artifacts_list_newest_first_and_fields(
    api, mock_config, tmp_path
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    mock_config.workspace_dir = tmp_path

    old = artifacts_dir / "2026-08-10-0900-old.md"
    old.write_text("old content")
    new = artifacts_dir / "2026-08-15-0010-мітинг.md"
    new.write_text("new content, longer")
    # Also drop a non-.md file and a subdirectory — neither should surface.
    (artifacts_dir / "notes.txt").write_text("ignore me")
    (artifacts_dir / "subdir").mkdir()

    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    resp = await api._handle_artifacts_list(_mock_request())
    data = json.loads(resp.body)
    assert data["ok"] is True
    names = [a["name"] for a in data["artifacts"]]
    assert names == ["2026-08-15-0010-мітинг.md", "2026-08-10-0900-old.md"]
    first = data["artifacts"][0]
    assert first["size"] == len("new content, longer".encode("utf-8"))
    assert isinstance(first["mtime"], float)


@pytest.mark.asyncio
async def test_artifacts_get_roundtrip_utf8(api, mock_config, tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    mock_config.workspace_dir = tmp_path
    body_text = "# Протокол мітингу\n\nОбговорили плани."
    (artifacts_dir / "2026-08-15-0010-мітинг.md").write_text(
        body_text, encoding="utf-8"
    )

    request = _mock_request()
    request.match_info = {"name": "2026-08-15-0010-мітинг.md"}
    resp = await api._handle_artifacts_get(request)
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    assert resp.charset == "utf-8"
    assert resp.text == body_text


@pytest.mark.asyncio
async def test_artifacts_get_missing_name_404(api, mock_config, tmp_path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    mock_config.workspace_dir = tmp_path

    request = _mock_request()
    request.match_info = {"name": "does-not-exist.md"}
    resp = await api._handle_artifacts_get(request)
    assert resp.status == 404


@pytest.mark.parametrize(
    "raw_name",
    ["..%2f", "../secret", "foo/bar", "..\\secret", ".."],
)
@pytest.mark.asyncio
async def test_artifacts_get_rejects_traversal(
    api, mock_config, tmp_path, raw_name
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    mock_config.workspace_dir = tmp_path

    request = _mock_request()
    request.match_info = {"name": raw_name}
    resp = await api._handle_artifacts_get(request)
    assert resp.status == 400
    assert json.loads(resp.body)["ok"] is False


@pytest.mark.asyncio
async def test_setup_config_preserves_config_toml_sections(
    mock_state, mock_config, tmp_path, monkeypatch
) -> None:
    """Regression: saving keys used to flatten config.toml, dropping the
    [browser_bridge] header and orphaning the bridge token (close 4001)."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(
        '[browser_bridge]\ntoken = "secret-token"\nconfirmation_passphrase = "фраза"\n'
    )
    api = API(mock_state, mock_config)

    await api._handle_setup_config(
        _mock_request(json_data={"language": "uk", "tts_voice": "uk-UA-OstapNeural"})
    )

    text = (tmp_path / "config.toml").read_text()
    assert "[browser_bridge]" in text
    assert 'token = "secret-token"' in text
    assert 'groq_language = "uk"' in text

    import tomllib

    parsed = tomllib.loads(text)
    assert parsed["browser_bridge"]["token"] == "secret-token"
    assert parsed["groq_language"] == "uk"


@pytest.fixture
def authorship_db(tmp_path):
    """Rows from both engines: pipecat marked agent rows mode='assistant',
    the spine writes mode='spine' on both sides and distinguishes with
    agent_spoken."""
    db_path = tmp_path / "authorship.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE transcripts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, text TEXT NOT NULL, "
        "mode TEXT NOT NULL, agent_spoken INTEGER, source TEXT)"
    )
    conn.execute(
        "CREATE TABLE actions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, status TEXT, "
        "result_summary TEXT, tool TEXT, args TEXT)"
    )
    conn.executemany(
        "INSERT INTO transcripts (ts, text, mode, agent_spoken) VALUES (?, ?, ?, ?)",
        [
            (100.0, "я спитав", "spine", 0),
            (101.0, "я відповів", "spine", 1),
            (102.0, "старий юзер", "ambient", 0),
            (103.0, "стара відповідь", "assistant", 1),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_activity_authorship_comes_from_agent_spoken(
    api, mock_config, authorship_db
) -> None:
    """Reading authorship from the polymorphic mode column labelled every
    spine reply as the user — the history showed the bot talking to
    itself as 'you'."""
    mock_config.db_path = authorship_db
    resp = await api._handle_activity(_mock_request())
    by_text = {r["content"]: r["who"] for r in json.loads(resp.body)}

    assert by_text["я спитав"] == "you"
    assert by_text["я відповів"] == "bot"
    assert by_text["старий юзер"] == "you"
    assert by_text["стара відповідь"] == "bot"


# ---------------------------------------------------------------------------
# Authentication middleware
#
# 48 routes sat on 127.0.0.1 with no check of any kind, and one of them
# (/inject) reaches the assistant and from there a shell. These exercise the
# real request chain — TestServer over the app src/main.py and src/menubar.py
# build — because the defect was never in a handler, it was in what was
# missing in front of them.
# ---------------------------------------------------------------------------

TOKEN = "test-token-0123456789abcdefghijkl"

_INDEX_DOC = "<html><head><title>Heare</title></head><body></body></html>"


@pytest.fixture
def auth_config(mock_config):
    mock_config.api_token = TOKEN
    return mock_config


@pytest.fixture
async def auth_client(mock_state, auth_config):
    """The app as the daemon assembles it: host serves /, API mounts the rest."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def _index(_request):
        return web.Response(text=_INDEX_DOC, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", _index)
    API(mock_state, auth_config).register_routes(app)

    client = TestClient(TestServer(app))
    await client.start_server()
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_auth_missing_token_is_401(auth_client) -> None:
    resp = await auth_client.get("/state")
    assert resp.status == 401
    assert await resp.json() == {"ok": False, "error": "unauthorized"}


@pytest.mark.asyncio
async def test_auth_wrong_token_is_401(auth_client) -> None:
    resp = await auth_client.get(
        "/state", headers={"Authorization": "Bearer not-the-token"}
    )
    assert resp.status == 401


@pytest.mark.asyncio
async def test_auth_header_token_is_accepted(auth_client) -> None:
    resp = await auth_client.get(
        "/state", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert resp.status == 200
    assert (await resp.json())["mode"] == "focus"


@pytest.mark.asyncio
async def test_auth_query_token_accepted_on_get(auth_client) -> None:
    """EventSource cannot set headers; /events would be unreachable without this."""
    resp = await auth_client.get("/state", params={"token": TOKEN})
    assert resp.status == 200


@pytest.mark.asyncio
async def test_auth_query_token_rejected_on_post(auth_client) -> None:
    """A write credential in a URL is a credential in a log and an <img src>."""
    resp = await auth_client.post("/state", params={"token": TOKEN}, json={})
    assert resp.status == 401
    assert (await resp.json())["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_auth_cross_origin_rejected_despite_valid_token(auth_client) -> None:
    """The actual attack: a page the user visits, POSTing to localhost."""
    resp = await auth_client.post(
        "/inject",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "https://evil.example",
        },
        json={"text": "run something"},
    )
    assert resp.status == 403
    assert (await resp.json())["ok"] is False


@pytest.mark.asyncio
async def test_auth_cross_origin_rejected_on_get_too(auth_client) -> None:
    resp = await auth_client.get(
        "/state",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://evil.example"},
    )
    assert resp.status == 403


@pytest.mark.asyncio
async def test_auth_same_origin_with_token_is_accepted(auth_client) -> None:
    resp = await auth_client.get(
        "/state",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": f"http://127.0.0.1:{auth_client.port}",
        },
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_auth_localhost_origin_is_accepted(auth_client) -> None:
    resp = await auth_client.get(
        "/state",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Origin": f"http://localhost:{auth_client.port}",
        },
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_auth_index_needs_no_token(auth_client) -> None:
    """The page has to load before it can read the token."""
    resp = await auth_client.get("/")
    assert resp.status == 200
    assert "Heare" in await resp.text()


@pytest.mark.asyncio
async def test_index_carries_the_token_to_the_page(auth_client) -> None:
    resp = await auth_client.get("/")
    body = await resp.text()
    assert f'window.__HEARE_TOKEN__ = "{TOKEN}"' in body
    assert body.index("__HEARE_TOKEN__") < body.index("</head>")


@pytest.mark.asyncio
async def test_auth_post_index_still_requires_a_token(auth_client) -> None:
    """GET / is exempt; the form that saves API keys is not."""
    resp = await auth_client.post("/", data={"deepseek_api_key": "sk-x"})
    assert resp.status == 401


@pytest.mark.asyncio
async def test_auth_no_configured_token_denies(mock_state, mock_config) -> None:
    """No token must mean no access — never open access."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    mock_config.api_token = ""
    mock_config.api_token_file = None
    app = web.Application()
    API(mock_state, mock_config).register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/state", headers={"Authorization": "Bearer "})
        assert resp.status == 401
    finally:
        await client.close()


def test_origin_is_local() -> None:
    from src.api import _origin_is_local

    assert _origin_is_local("http://127.0.0.1:9780")
    assert _origin_is_local("http://localhost:5173")
    assert _origin_is_local("http://[::1]:9780")
    assert not _origin_is_local("https://evil.example")
    assert not _origin_is_local("http://127.0.0.1.evil.example")
    assert not _origin_is_local("null")
    assert not _origin_is_local("")


def test_inject_token_html_is_idempotent() -> None:
    from src.api import _inject_token_html

    once = _inject_token_html(_INDEX_DOC, TOKEN)
    assert once.count("__HEARE_TOKEN__") == 1
    assert _inject_token_html(once, TOKEN) == once
    assert _inject_token_html(_INDEX_DOC, "") == _INDEX_DOC


# ---------------------------------------------------------------------------
# GET/POST /api/features — the spine's subsystem switches
#
# Two truths that must stay apart: what the engine wired at its last start
# (the `spine_features` State key) and what config.toml asks for next time.
# The write path is the interesting one — it edits a file the user also
# hand-edits, so "wrote only that key" is the property under test.
# ---------------------------------------------------------------------------

_CONFIG_FIXTURE = """\
# a comment the user wrote and must keep
engine = "spine"

[browser_bridge]
token = "secret-token"
"""


def _with_features(literal: str) -> str:
    """The fixture file with a `spine_features` line in its preamble — the
    only place a top-level key can live, above the first [table] header."""
    return _CONFIG_FIXTURE.replace(
        "\n[browser_bridge]", f"spine_features = {literal}\n\n[browser_bridge]"
    )


@pytest.fixture
def features_home(tmp_path, monkeypatch):
    """A real config.toml with a comment, another key and a section table."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)
    (tmp_path / "config.toml").write_text(_CONFIG_FIXTURE)
    return tmp_path


def _features_client(state, config):
    """A TestClient over the assembled app, as the daemon builds it."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    app = web.Application()
    API(state, config).register_routes(app)
    return TestClient(TestServer(app))


@pytest.fixture
async def features_client(mock_state, auth_config):
    client = _features_client(mock_state, auth_config)
    await client.start_server()
    yield client
    await client.close()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_features_unknown_name_is_400(features_client, features_home) -> None:
    resp = await features_client.post(
        "/api/features", headers=_auth(), json={"name": "nosuch", "enabled": False}
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["ok"] is False
    assert "roles" in body["known"]
    # And nothing was written on the way out.
    assert (features_home / "config.toml").read_text() == _CONFIG_FIXTURE


@pytest.mark.asyncio
async def test_features_non_boolean_is_400(features_client, features_home) -> None:
    resp = await features_client.post(
        "/api/features", headers=_auth(), json={"name": "roles", "enabled": "no"}
    )
    assert resp.status == 400
    assert (features_home / "config.toml").read_text() == _CONFIG_FIXTURE


@pytest.mark.asyncio
async def test_features_toggle_writes_only_that_key(
    features_client, features_home
) -> None:
    """The comment, the other key and the [browser_bridge] table all survive —
    flattening config.toml is how the bridge token got orphaned once already."""
    import tomllib

    resp = await features_client.post(
        "/api/features", headers=_auth(), json={"name": "roles", "enabled": False}
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["config"] == {"roles": False}
    assert body["restart_required"] is True

    text = (features_home / "config.toml").read_text()
    assert "# a comment the user wrote and must keep" in text
    assert 'engine = "spine"' in text
    assert "[browser_bridge]" in text

    parsed = tomllib.loads(text)
    # An inline table, not a string: features.resolve ignores anything else.
    assert parsed["spine_features"] == {"roles": False}
    assert parsed["engine"] == "spine"
    assert parsed["browser_bridge"]["token"] == "secret-token"


@pytest.mark.asyncio
async def test_features_second_toggle_merges(features_client, features_home) -> None:
    import tomllib

    await features_client.post(
        "/api/features", headers=_auth(), json={"name": "roles", "enabled": False}
    )
    resp = await features_client.post(
        "/api/features", headers=_auth(), json={"name": "mcp", "enabled": False}
    )
    assert (await resp.json())["config"] == {"roles": False, "mcp": False}

    text = (features_home / "config.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["spine_features"] == {"roles": False, "mcp": False}
    # One key, one line — the merge must not leave a second spine_features.
    assert text.count("spine_features") == 1
    assert parsed["browser_bridge"]["token"] == "secret-token"


@pytest.mark.asyncio
async def test_features_get_returns_live_and_config(
    mock_state, auth_config, features_home
) -> None:
    mock_state.snapshot.return_value = {
        "spine_features": json.dumps(
            [
                {"name": "roles", "on": True, "cost": "мітинг недоступний"},
                {"name": "mcp", "on": False, "cost": "MCP не підключається"},
            ],
            ensure_ascii=False,
        )
    }
    (features_home / "config.toml").write_text(
        _with_features("{ roles = false }")
    )
    client = _features_client(mock_state, auth_config)
    await client.start_server()
    try:
        resp = await client.get("/api/features", headers=_auth())
        assert resp.status == 200
        body = await resp.json()
        # Both halves, so the widget never has to guess one from the other:
        # roles runs but is configured off — that row needs a restart.
        assert body["live"][0] == {
            "name": "roles",
            "on": True,
            "cost": "мітинг недоступний",
        }
        assert body["config"] == {"roles": False}
        assert body["defaults"]["roles"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_features_get_without_engine_report(features_client, features_home):
    """The pipecat engine (or an old daemon) writes no such key: say so with
    null rather than passing the config off as what is running."""
    resp = await features_client.get("/api/features", headers=_auth())
    body = await resp.json()
    assert body["live"] is None
    assert body["config"] == {}


@pytest.mark.asyncio
async def test_features_ignores_junk_in_config(features_client, features_home) -> None:
    """A hand-edited file may hold names and types resolve() would ignore;
    the dashboard must not show a value the engine will never honour."""
    (features_home / "config.toml").write_text(
        _with_features('{ roles = false, nosuch = true, mcp = "off" }')
    )
    resp = await features_client.get("/api/features", headers=_auth())
    assert (await resp.json())["config"] == {"roles": False}


@pytest.mark.asyncio
async def test_features_requires_auth(features_client, features_home) -> None:
    resp = await features_client.get("/api/features")
    assert resp.status == 401
    resp = await features_client.post(
        "/api/features", json={"name": "roles", "enabled": False}
    )
    assert resp.status == 401
    assert (features_home / "config.toml").read_text() == _CONFIG_FIXTURE
