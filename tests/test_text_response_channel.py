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
    props = _spec.schema_fields
    assert "content" in props
    assert "title" in props
