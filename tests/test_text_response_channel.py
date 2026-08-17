"""Text-response channel — storage columns + latest_bot_response()."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.store.storage import TranscriptStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = TranscriptStore(Path(tmp) / "h.db")
        await s.init()
        try:
            yield s
        finally:
            await s.close()


async def test_agent_mode_spoken_round_trip(store: TranscriptStore) -> None:
    await store.log_transcript(
        "hello there",
        "assistant",
        agent_mode="silent",
        agent_spoken=False,
    )
    r = await store.latest_bot_response()
    assert r is not None
    assert r["text"] == "hello there"
    assert r["agent_mode"] == "silent"
    assert r["agent_spoken"] is False


async def test_latest_bot_response_picks_newest_bot_row(
    store: TranscriptStore,
) -> None:
    await store.log_transcript("old", "assistant",
                                agent_mode="ambient", agent_spoken=True)
    await store.log_transcript("a user turn", "ambient")
    await store.log_transcript("newest", "assistant",
                                agent_mode="focus", agent_spoken=True)
    r = await store.latest_bot_response()
    assert r["text"] == "newest"
    assert r["agent_mode"] == "focus"
    assert r["agent_spoken"] is True


async def test_latest_bot_response_none_when_no_bot_rows(
    store: TranscriptStore,
) -> None:
    await store.log_transcript("just a user", "ambient")
    assert await store.latest_bot_response() is None


async def test_agent_columns_default_null(store: TranscriptStore) -> None:
    await store.log_transcript("legacy bot", "assistant")
    r = await store.latest_bot_response()
    assert r["agent_mode"] is None
    assert r["agent_spoken"] is None


async def test_fetch_agent_response_reads_latest_bot_row(store):
    """Dashboard sync read path returns the newest bot row with tags."""
    import sqlite3

    from src.dashboard_data import fetch_agent_response

    await store.log_transcript("user said hi", "ambient")
    await store.log_transcript(
        "I won't say this aloud",
        "assistant",
        agent_mode="silent",
        agent_spoken=False,
    )
    await store.close()

    con = sqlite3.connect(str(store.db_path))
    try:
        r = fetch_agent_response(con)
    finally:
        con.close()
    assert r.text == "I won't say this aloud"
    assert r.mode == "silent"
    assert r.spoken is False


def test_fetch_agent_response_empty_on_none_con():
    from src.dashboard_data import fetch_agent_response

    r = fetch_agent_response(None)
    assert r.text is None and r.spoken is None


def test_dashboard_snapshot_has_agent_response_field():
    from src.dashboard_data import DashboardSnapshot

    assert "agent_response" in DashboardSnapshot.__dataclass_fields__


async def test_log_and_latest_display(store: TranscriptStore) -> None:
    await store.log_display("print('hi')", "code", title="snippet")
    await store.log_display("+---+\n| x |\n+---+", "ascii", title="box")
    d = await store.latest_display()
    assert d["content"] == "+---+\n| x |\n+---+"
    assert d["format"] == "ascii"
    assert d["title"] == "box"


async def test_latest_display_none_when_empty(store: TranscriptStore) -> None:
    assert await store.latest_display() is None


async def test_show_display_tool_persists(store: TranscriptStore) -> None:
    import json

    from src.agent.tools.direct import _execute_show_display
    from src.config import load_settings

    s = load_settings()
    s.db_path = store.db_path
    res = await _execute_show_display(
        json.dumps({"content": "def f():\n  pass", "format": "code",
                    "title": "fn"}),
        s,
    )
    assert res["success"] is True
    d = await store.latest_display()
    assert d["content"] == "def f():\n  pass"
    assert d["format"] == "code"
    assert d["title"] == "fn"


async def test_show_display_rejects_empty_content(store: TranscriptStore) -> None:
    import json
    from src.agent.tools.direct import _execute_show_display
    from src.config import load_settings

    s = load_settings()
    s.db_path = store.db_path
    res = await _execute_show_display(json.dumps({"content": "", "format": "code"}), s)
    assert res["success"] is False


async def test_show_display_unknown_format_falls_back_text(
    store: TranscriptStore,
) -> None:
    import json
    from src.agent.tools.direct import _execute_show_display
    from src.config import load_settings

    s = load_settings()
    s.db_path = store.db_path
    await _execute_show_display(
        json.dumps({"content": "x", "format": "weird"}), s
    )
    d = await store.latest_display()
    assert d["format"] == "text"


def test_show_display_registered():
    from src.agent.tools.system import get_tool, get_tool_names
    
    # show_display was replaced by show_text + show_canvas in the new definitions
    assert "show_text" in get_tool_names()
    tool = get_tool("show_text")
    assert tool is not None
    assert tool.name == "show_text"
    assert tool.handler == "display"
    _spec = get_tool("show_text")
    props, required = _spec.schema_fields, _spec.required
    assert "content" in props
    assert "title" in props


def test_fetch_latest_display_empty_on_none():
    from src.dashboard_data import fetch_latest_display
    d = fetch_latest_display(None)
    assert d.content is None and d.fmt == "text"


async def test_fetch_latest_display_reads_row(store: TranscriptStore) -> None:
    import sqlite3
    from src.dashboard_data import fetch_latest_display

    await store.log_display("graph TD", "markdown", title="diagram")
    await store.close()
    con = sqlite3.connect(str(store.db_path))
    try:
        d = fetch_latest_display(con)
    finally:
        con.close()
    assert d.content == "graph TD"
    assert d.fmt == "markdown"
    assert d.title == "diagram"


def test_dashboard_snapshot_has_display_field():
    from src.dashboard_data import DashboardSnapshot
    assert "display" in DashboardSnapshot.__dataclass_fields__
