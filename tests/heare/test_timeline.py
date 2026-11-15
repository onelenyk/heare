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


def test_show_keeps_the_script_so_the_page_can_run_the_app(tmp_path):
    import asyncio

    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    said = asyncio.run(timeline._show({
        "html": '<h3>Mini app</h3><style>#x{animation:pulse 1s infinite}</style>'
                '<script>document.getElementById("x").textContent = "ran"</script>',
        "say": "Ось застосунок."}))
    row = sessions.read(session)[0]
    assert said == "Ось застосунок."
    assert "<script>" in row["html"] and "<style>" in row["html"]
    assert row["artifact"]["live"] is True


def test_show_page_reads_only_a_page_under_the_work_root(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "heare-work"
    work.mkdir()
    (work / "app.html").write_text(
        "<!doctype html><html><head><style>#x{color:#fff}</style></head>"
        "<body><h3>App</h3><p id='x'>no</p>"
        "<script>document.getElementById('x').textContent='ran'</script></body></html>",
        encoding="utf-8")
    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    assert asyncio.run(timeline._show_page({"file": "app.html", "say": "Ось."})) == "Ось."
    row = sessions.read(session)[0]
    assert row["kind"] == "html" and "<script>" in row["html"] and "<style>" in row["html"]
    assert row["artifact"]["label"] == "app" and row["width"] == "w"
    # nothing outside the work root, and nothing that is not a page
    (tmp_path / "secret.html").write_text("<p>x</p>", encoding="utf-8")
    before = len(sessions.read(session))
    assert asyncio.run(timeline._show_page({"file": "../secret.html", "say": "x"}))
    assert asyncio.run(timeline._show_page({"file": str(tmp_path / "secret.html"), "say": "x"}))
    (work / "notes.txt").write_text("x", encoding="utf-8")
    assert asyncio.run(timeline._show_page({"file": "notes.txt", "say": "x"}))
    assert len(sessions.read(session)) == before


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


def test_a_part_s_card_is_a_message_the_page_draws(tmp_path):
    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    shown = timeline.from_part({"kind": "html", "html": "<h3>Cat</h3><svg></svg>",
                                "say": "Ось кіт."})
    row = sessions.read(session)[0]
    assert shown["shown"] == row["id"]
    assert row["kind"] == "html" and row["text"] == "Ось кіт."
    assert row["artifact"]["title"] == "Cat"


def test_a_diff_card_from_the_hands_is_a_message_the_page_draws(tmp_path):
    from heare.reach import changes

    sessions = Sessions(tmp_path / "s")
    sessions.new()
    timeline = Timeline(sessions)
    body = changes.card([
        {"file": "src/app.py", "additions": 3, "deletions": 1,
         "patch": "-old\n+new\n", "status": "modified"},
    ])

    shown = timeline.from_part(body)
    row = sessions.read(sessions.active)[0]

    assert shown["shown"] == row["id"]
    assert row["kind"] == "html"
    assert row["text"] == "Змінив app.py: +3 −1."
    assert row["artifact"]["title"] == "app.py"
    assert "$" not in row["html"]  # nothing fetched, nothing executed
    assert '<div class="kv">' in row["html"]


def test_a_part_s_picture_is_kept_beside_the_session(tmp_path):
    import base64

    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    timeline = Timeline(sessions)
    raw = b"\x89PNG\r\n" + b"x" * 4
    timeline.from_part({"kind": "image", "name": "cat.png",
                        "data": base64.b64encode(raw).decode()})
    row = sessions.read(session)[0]
    assert row["kind"] == "image" and row["artifact"]["mime"] == "image/png"
    meta, path = sessions.stored(session, row["artifact"]["file"])
    assert meta["kind"] == "image" and path.read_bytes() == raw


def test_a_part_can_show_a_link_but_not_a_file_url(tmp_path):
    import pytest

    sessions = Sessions(tmp_path / "s")
    sessions.new()
    timeline = Timeline(sessions)
    timeline.from_part({"kind": "link", "url": "https://example.com", "title": "x"})
    with pytest.raises(ValueError):
        timeline.from_part({"kind": "link", "url": "file:///etc/passwd"})


def test_a_part_cannot_show_a_kind_the_page_does_not_draw(tmp_path):
    import pytest

    sessions = Sessions(tmp_path / "s")
    sessions.new()
    timeline = Timeline(sessions)
    with pytest.raises(ValueError):
        timeline.from_part({"kind": "smell", "html": "<p>x</p>"})
    with pytest.raises(ValueError):
        timeline.from_part({"kind": "image", "name": "cat.png", "data": "not base64!"})


def test_a_part_cannot_show_more_than_a_cap(tmp_path, monkeypatch):
    import base64

    import pytest

    from heare.api import timeline as window

    monkeypatch.setattr(window, "PART_BYTES", 4)
    sessions = Sessions(tmp_path / "s")
    sessions.new()
    timeline = Timeline(sessions)
    with pytest.raises(ValueError):
        timeline.from_part({"kind": "image", "name": "cat.png",
                            "data": base64.b64encode(b"x" * 16).decode()})
