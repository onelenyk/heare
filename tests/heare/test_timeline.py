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
