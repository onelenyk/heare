"""The timeline: messages over a session, and the old canvas as one view of them."""

from __future__ import annotations

import json

from heare.api.sessions import Sessions
from heare.api.timeline import Timeline


def test_the_old_canvas_becomes_messages_once(tmp_path):
    canvas = tmp_path / "canvas.json"
    canvas.write_text(json.dumps({
        "blocks": [["a", "<h2>План</h2>"], ["b", "<p>b</p>", "m"]], "css": "x"}),
        encoding="utf-8")
    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions, canvas_file=canvas)
    assert timeline.migrate_legacy() == 2
    rows = sessions.read(session)
    assert [row["kind"] for row in rows] == ["html", "html"]
    assert rows[0]["artifact"]["block"] == "a" and rows[0]["width"] == "s"
    assert rows[1]["width"] == "w" and rows[1]["artifact"]["title"]
    assert timeline.migrate_legacy() == 0 and len(sessions.read(session)) == 2
    assert timeline.canvas_view()["blocks"][0]["id"] == "a"
    assert timeline.canvas_view()["css"] == "x"


def test_the_canvas_shims_are_messages(tmp_path):
    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    timeline.canvas_put("a", "<p>a</p>")
    assert sessions.read(session)[0]["kind"] == "html"
    timeline.canvas_put("a", "<p>a2</p>")
    assert sessions.read(session)[0]["html"] == "<p>a2</p>"
    assert len(sessions.read(session)) == 1
    assert timeline.canvas_remove("a")
    assert sessions.read(session)[0]["status"] == "withdrawn"
    assert timeline.canvas_view()["blocks"] == []


def test_show_puts_a_card_and_the_context_names_it(tmp_path):
    import asyncio

    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    said = asyncio.run(timeline._show({
        "html": "<table><tr><td>a</td></tr></table>", "say": "Ось таблиця."}))
    rows = sessions.read(session)
    assert said == "Ось таблиця."
    assert rows[0]["kind"] == "html" and rows[0]["width"] == "w"
    assert rows[0]["artifact"]["label"] == "table"
    assert rows[0]["id"] in timeline.context()


def test_show_link_and_media_are_checked(tmp_path):
    import asyncio

    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    assert asyncio.run(timeline._show_link({"url": "javascript:x", "say": "x"}))
    assert sessions.read(session) == []
    asyncio.run(timeline._show_link({"url": "https://example.com/a", "say": "Лінк."}))
    assert sessions.read(session)[0]["kind"] == "link"
    assert asyncio.run(timeline._show_media({"file": "0" * 32, "kind": "image", "say": "x"}))
    assert len(sessions.read(session)) == 1
